"""Integration tests: full orchestration loop with FakeMCPManager + MockLLM.

Covers: happy-path 2-agent DAG with real graph execution + event stream,
tool-failure resilience (digest-based degradation, no crash), degraded planning
mode, and permission enforcement.
"""

from __future__ import annotations

import asyncio

import pytest
from app.agents.factory import AgentFactory
from app.ai.provider import MockLLM
from app.models import db
from app.models.schemas import Plan, PlanStep
from app.observability.bus import EventBus
from app.orchestration.workflow.executor import Executor, drain_post_runs


def _plan() -> Plan:
    return Plan(thread_id="t-int", goal="Predict the velocity for our next sprint",
                steps=[
                    PlanStep(id="s1", agent="jira-analyst",
                             task="fetch sprints",
                             tools=["jira_get_sprints"], depends_on=[]),
                    PlanStep(id="s2", agent="data-scientist",
                             task="forecast", tools=["run_python"],
                             depends_on=["s1"]),
                ],
                synthesis="combine")


@pytest.fixture
def bus():
    return EventBus()


async def _execute(registry, manager, bus) -> tuple[str, list[dict]]:
    await db.init_schema()
    session = await db.create_session_record("integration", "demo")
    await db.create_run_record(
        session.id, "t-int", "Predict the velocity for our next sprint",
        run_id="run-1")
    executor = Executor(AgentFactory(registry, manager, MockLLM()), bus)
    active = await executor.execute(
        run_id="run-1", session_id=session.id, thread_id="t-int",
        goal="Predict the velocity for our next sprint", plan=_plan())
    await drain_post_runs()
    rows = await db.list_traces("run-1")
    return active.status, [row.payload for row in rows]


def test_full_run_completes_with_expected_event_sequence(registry, fake_manager, bus) -> None:
    status, traces = asyncio.run(_execute(registry, fake_manager, bus))
    assert status == "completed"
    types = [t["type"] for t in traces]
    assert "run_finished" in types
    assert "plan_created" not in types  # plan events are emitted by the pipeline
    assert "agent_spawned" in types and "node_started" in types
    assert "tool_call_started" in types and "tool_call_finished" in types


def test_tool_failure_degrades_without_crashing_run(registry, bus) -> None:
    from tests.fakes import FakeMCPManager

    failing = FakeMCPManager(fail_tools={"jira_get_projects"})
    plan = Plan(
        thread_id="t-int",
        goal="List all accessible Jira projects",
        steps=[
            PlanStep(
                id="s1",
                agent="jira-analyst",
                task="List all accessible Jira projects",
                depends_on=[]),
        ],
        synthesis="Report project list or fetch failure.",
    )

    async def _run() -> tuple[str, list[dict]]:
        await db.init_schema()
        session = await db.create_session_record("integration", "demo")
        await db.create_run_record(
            session.id, "t-int", plan.goal, run_id="run-tool-fail")
        executor = Executor(AgentFactory(registry, failing, MockLLM()), bus)
        active = await executor.execute(
            run_id="run-tool-fail", session_id=session.id, thread_id="t-int",
            goal=plan.goal, plan=plan)
        await drain_post_runs()
        rows = await db.list_traces("run-tool-fail")
        return active.status, [row.payload for row in rows]

    status, traces = asyncio.run(_run())
    assert status == "completed"
    finished = [
        event for event in traces
        if event.get("type") == "tool_call_finished" and event.get("status") == "error"
    ]
    assert finished


async def test_active_sprint_ticket_answer_is_user_facing(
        registry, fake_manager, bus) -> None:
    await db.init_schema()
    session = await db.create_session_record("tickets", "demo")
    run_id = "run-active-tickets"
    goal = "What are the open Jira tickets in this sprint?"
    await db.create_run_record(
        session.id, session.thread_id, goal, run_id=run_id)
    executor = Executor(AgentFactory(registry, fake_manager, MockLLM()), bus)
    plan = Plan(
        thread_id=session.thread_id,
        goal=goal,
        steps=[PlanStep(
            id="s1", agent="jira-analyst", task="List open tickets in the active sprint",
            tools=["jira_get_sprints", "jira_get_sprint_issues"], depends_on=[])],
        synthesis="Answer with a concise ticket list.",
    )

    active = await executor.execute(
        run_id=run_id, session_id=session.id, thread_id=session.thread_id,
        goal=goal, plan=plan)
    await drain_post_runs()
    run = await db.get_run_record(run_id)

    assert active.status == "completed"
    assert run is not None and run.result_summary is not None
    assert "PLAT-9" in run.result_summary
    assert "Stabilize checkout retries" in run.result_summary
    assert "Step completed using available tools" not in run.result_summary
    tools_called = [name for name, _args in fake_manager.calls]
    assert "jira_get_sprint_issues" in tools_called
    assert "jira_search" not in tools_called


def test_permission_violation_blocked(fake_manager) -> None:
    import asyncio

    import pytest
    from app.core.errors import MCPToolPermissionError
    from app.models.schemas import AgentTemplate

    template = AgentTemplate(name="x", description="d", system_prompt="p",
                             allowed_tools=["run_python"])
    with pytest.raises(MCPToolPermissionError):
        asyncio.run(fake_manager.call_tool_for_agent(
            tool_name="jira_search", args={}, agent_template=template,
            step_id="s", run_id="r"))


def test_degraded_plan_still_answers(registry, fake_manager, bus) -> None:
    asyncio.run(db.init_schema())
    session = asyncio.run(db.create_session_record("degraded", "demo"))
    asyncio.run(db.create_run_record(
        session.id, "t-d", "something unplanable", run_id="run-degraded"))
    executor = Executor(AgentFactory(registry, fake_manager, MockLLM()), bus)
    active = asyncio.run(executor.execute(
        run_id="run-degraded", session_id=session.id, thread_id="t-d",
        goal="something unplanable", plan=None))
    assert active.status == "completed"
    asyncio.run(drain_post_runs())


def _parallel_sibling_overlap(events: list[dict], step_a: str, step_b: str) -> bool:
    """True when both steps started before either finished (DAG-level parallel)."""
    def _indices(etype: str, node: str) -> list[int]:
        return [
            index for index, event in enumerate(events)
            if event.get("type") == etype and event.get("node") == node
        ]

    started = _indices("node_started", step_a) + _indices("node_started", step_b)
    finished = _indices("node_finished", step_a) + _indices("node_finished", step_b)
    if len(started) < 2 or len(finished) < 2:
        return False
    return max(started) < min(finished)


def test_parallel_dag_sibling_steps_overlap(registry, fake_manager, bus) -> None:
    asyncio.run(db.init_schema())
    session = asyncio.run(db.create_session_record("parallel", "demo"))
    goal = "Compare PLAT and NOVA project health side by side"
    run_id = "run-parallel-dag"
    asyncio.run(db.create_run_record(session.id, "t-par", goal, run_id=run_id))
    plan = Plan(
        thread_id="t-par",
        goal=goal,
        steps=[
            PlanStep(id="s1", agent="jira-analyst",
                     task="Fetch PLAT project health metrics", depends_on=[]),
            PlanStep(id="s2", agent="jira-analyst",
                     task="Fetch NOVA project health metrics", depends_on=[]),
        ],
        synthesis="Compare PLAT vs NOVA using both step outputs.",
    )
    executor = Executor(AgentFactory(registry, fake_manager, MockLLM()), bus)
    active = asyncio.run(executor.execute(
        run_id=run_id, session_id=session.id, thread_id="t-par",
        goal=goal, plan=plan))
    traces = asyncio.run(db.list_traces(run_id))
    events = [row.payload for row in traces]
    assert active.status == "completed"
    assert _parallel_sibling_overlap(events, "s1", "s2")
    spawns = {event.get("step_id") for event in events if event.get("type") == "agent_spawned"}
    assert spawns == {"s1", "s2"}
    asyncio.run(drain_post_runs())


def test_mcp_unavailable_graceful_answer(registry, bus) -> None:
    from tests.fakes import FakeMCPManager

    asyncio.run(db.init_schema())
    session = asyncio.run(db.create_session_record("mcp-down", "demo"))
    run_id = "run-mcp-down"
    goal = "What Jira projects exist?"
    asyncio.run(db.create_run_record(session.id, "t-mcp", goal, run_id=run_id))
    manager = FakeMCPManager(servers_down={"jira"})
    executor = Executor(AgentFactory(registry, manager, MockLLM()), bus)
    active = asyncio.run(executor.execute_mcp_unavailable(
        run_id=run_id, session_id=session.id, thread_id="t-mcp",
        goal=goal, unavailable=["jira"]))
    run = asyncio.run(db.get_run_record(run_id))
    assert active.status == "completed"
    assert run is not None and run.result_summary
    lowered = run.result_summary.lower()
    assert any(word in lowered for word in ("unavailable", "cannot", "retry", "service"))
    usage = run.token_usage or {}
    assert usage.get("mcp_unavailable") == ["jira"]


@pytest.mark.parametrize("approved,expected_calls", [(True, 1), (False, 0)])
async def test_sensitive_tool_waits_for_human_decision(
        registry, bus, approved: bool, expected_calls: int) -> None:
    """Agent-level approval policy remains effective even if server config drifts."""
    from tests.fakes import FakeMCPManager

    await db.init_schema()
    session = await db.create_session_record("approval", "demo")
    run_id = f"run-approval-{approved}"
    await db.create_run_record(session.id, "t-approval", "create issue", run_id=run_id)
    manager = FakeMCPManager()
    executor = Executor(AgentFactory(registry, manager, MockLLM()), bus)
    plan = Plan(
        thread_id="t-approval",
        goal="create issue",
        steps=[PlanStep(
            id="s1", agent="jira-writer", task="create issue",
            tools=["jira_create_issue"], depends_on=[])],
        synthesis="report the outcome",
    )

    task = asyncio.create_task(executor.execute(
        run_id=run_id, session_id=session.id, thread_id="t-approval",
        goal="create issue", plan=plan))
    for _ in range(100):
        run = await db.get_run_record(run_id)
        if run is not None and run.status == "awaiting_approval":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("run never entered awaiting_approval")

    assert executor.resolve_approval(run_id, approved)
    await db.update_run_record(run_id, status="running")
    active = await asyncio.wait_for(task, timeout=15)
    await drain_post_runs()
    assert active.status == "completed"
    assert len([call for call in manager.calls if call[0] == "jira_create_issue"]) == expected_calls


async def test_pipeline_crash_marks_run_failed(monkeypatch, bus) -> None:
    from types import SimpleNamespace

    from app.services import chat_service

    async def broken_router(*_args, **_kwargs):
        raise RuntimeError("private provider detail")

    monkeypatch.setattr("app.orchestration.workflow.pipeline.classify", broken_router)
    await db.init_schema()
    session = await db.create_session_record("failure", "demo")
    await db.create_run_record(
        session.id, session.thread_id, "fail safely", run_id="run-pipeline-failure")
    await chat_service._run_pipeline(
        run_id="run-pipeline-failure", session_id=session.id,
        thread_id=session.thread_id, message="fail safely",
        executor=SimpleNamespace(), bus=bus,
        platform=SimpleNamespace(factory=SimpleNamespace(provider=MockLLM())))

    run = await db.get_run_record("run-pipeline-failure")
    traces = await db.list_traces("run-pipeline-failure")
    assert run is not None and run.status == "failed"
    assert run.error == "The request could not be completed."
    assert traces[-1].event_type == "run_failed"


async def test_execute_returns_before_memory_pipeline(
        registry, fake_manager, bus, monkeypatch) -> None:
    """Voyage / fact extraction must not hold the chat path after run_finished."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_post_run(self, *args, **kwargs):  # noqa: ANN001
        started.set()
        await release.wait()

    monkeypatch.setattr(Executor, "_post_run", _blocked_post_run)
    await db.init_schema()
    session = await db.create_session_record("post-run", "demo")
    run_id = "run-post-run-offpath"
    await db.create_run_record(
        session.id, session.thread_id, "open tickets", run_id=run_id)
    executor = Executor(AgentFactory(registry, fake_manager, MockLLM()), bus)
    plan = Plan(
        thread_id=session.thread_id,
        goal="What are the open Jira tickets in this sprint?",
        steps=[PlanStep(
            id="s1", agent="jira-analyst",
            task="List open tickets in the active sprint",
            tools=["jira_get_sprints", "jira_get_sprint_issues"],
            depends_on=[])],
        synthesis="Answer with a concise ticket list.",
    )

    active = await asyncio.wait_for(
        executor.execute(
            run_id=run_id, session_id=session.id, thread_id=session.thread_id,
            goal="What are the open Jira tickets in this sprint?", plan=plan),
        timeout=10)
    assert active.status == "completed"
    traces = [row.payload for row in await db.list_traces(run_id)]
    assert any(row["type"] == "run_finished" for row in traces)
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await drain_post_runs()
