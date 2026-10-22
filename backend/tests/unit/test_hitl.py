"""Unit tests for HITL helpers — approval policy and clarify interrupt shape."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.schemas import AgentPermissions, AgentTemplate
from app.orchestration.hitl import approval_required, clarify_interrupt_metadata
from app.orchestration.workflow.clarify import coerce_plan_after_clarify, user_already_clarified


class _Sensitivity:
    def __init__(self, sensitive: set[str]) -> None:
        self._sensitive = sensitive

    def is_tool_sensitive(self, tool_name: str) -> bool:
        return tool_name in self._sensitive


def test_approval_required_from_agent_policy() -> None:
    agent = AgentTemplate(
        name="writer", description="d", system_prompt="p",
        allowed_tools=["jira_create_issue"],
        permissions=AgentPermissions(requires_approval_tools=["jira_create_issue"]),
    )
    manager = _Sensitivity(set())
    assert approval_required("jira_create_issue", agent=agent, manager=manager)
    assert not approval_required("jira_search", agent=agent, manager=manager)


def test_approval_required_from_mcp_catalog() -> None:
    agent = AgentTemplate(
        name="writer", description="d", system_prompt="p",
        allowed_tools=["jira_create_issue"],
    )
    manager = _Sensitivity({"jira_create_issue"})
    assert approval_required("jira_create_issue", agent=agent, manager=manager)


def test_clarify_interrupt_metadata_shape() -> None:
    meta = clarify_interrupt_metadata(
        goal="How's the project?",
        question="Which project?",
        conversation="User: How's the project?",
        replan_count=1,
    )
    assert meta["kind"] == "clarify"
    assert meta["question"] == "Which project?"
    assert meta["goal"] == "How's the project?"
    assert meta["replan_count"] == 1


def test_user_already_clarified() -> None:
    assert not user_already_clarified("What are open tickets?")
    assert user_already_clarified("Goal\n\nUser clarification: NOVA")


def test_coerce_plan_after_clarify_builds_analyst_step() -> None:
    from app.models.plan import Plan

    goal = "What are open tickets?\n\nUser clarification: NOVA project"
    plan = Plan(
        thread_id="t1",
        goal=goal,
        clarify_question="Which project?",
        synthesis="Summarize open issues",
    )
    coerced = coerce_plan_after_clarify(goal, "t1", plan)
    assert coerced.clarify_question is None
    assert len(coerced.steps) == 1
    assert coerced.steps[0].agent == "jira-analyst"
    assert "NOVA" in coerced.steps[0].task
