"""LangGraph builder — fresh compiled graph per request."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.factory import AgentFactory, SpawnedAgent
from app.mcp_integration.manager import MCPManager
from app.models import db
from app.models.plan import Plan, PlanStep
from app.observability.events import EventType, make_event
from app.orchestration.coverage.entity_coverage import synthesis_entity_gaps
from app.orchestration.graph.synthesis import compose_synthesis_prompt
from app.orchestration.graph.worker import emit_usage, execute_worker_step
from app.orchestration.state import OrchestratorState

logger = logging.getLogger(__name__)


class GraphBundle:
    """Compiled per-request graph + spawned agents + HITL approval gate."""

    def __init__(self, graph: Any, agents: dict[str, SpawnedAgent],
                 approved_tools: set[str]) -> None:
        self.graph = graph
        self.agents = agents
        self.approved_tools = approved_tools
        self.rejected_tools: set[str] = set()
        self.pending_tool: str | None = None
        self.approval_outcome: bool | None = None
        self._approval_event = asyncio.Event()
        self.emit: Any = None
        self.run_id: str = ""
        self.answer_streamed: bool = False
        self.on_awaiting_approval: Any = None
        self.db_session: AsyncSession | None = None

    async def request_approval(self, tool: str) -> bool:
        if tool in self.approved_tools:
            return True
        if tool in self.rejected_tools:
            return False
        self.pending_tool = tool
        self.approval_outcome = None
        if self.emit is not None:
            await self.emit(make_event(EventType.APPROVAL_REQUIRED, self.run_id,
                                       pending_tool=tool,
                                       reason="tool requires human approval"))
        await db.update_run_record(
            self.run_id, status="awaiting_approval", session=self.db_session)
        if self.on_awaiting_approval is not None:
            self.on_awaiting_approval()
        await self._approval_event.wait()
        approved = self.approval_outcome is True
        self._approval_event.clear()
        self.pending_tool = None
        if approved:
            self.approved_tools.add(tool)
        else:
            self.rejected_tools.add(tool)
        return approved

    def resolve_approval(self, approved: bool) -> None:
        self.approval_outcome = approved
        self._approval_event.set()


def _terminal_step_ids(plan: Plan) -> set[str]:
    """Leaf steps in the DAG — only step when single-agent lookup."""
    if len(plan.steps) == 1:
        return {plan.steps[0].id}
    dependents: set[str] = set()
    for step in plan.steps:
        dependents.update(step.depends_on)
    return {step.id for step in plan.steps if step.id not in dependents}


def build_graph(plan: Plan, factory: AgentFactory, manager: MCPManager, *,
                run_id: str, thread_id: str,
                checkpointer: Any | None = None,
                db_session: AsyncSession | None = None) -> GraphBundle:
    agents: dict[str, SpawnedAgent] = {}
    bundle = GraphBundle(graph=None, agents=agents, approved_tools=set())
    bundle.run_id = run_id
    bundle.db_session = db_session
    terminal_ids = _terminal_step_ids(plan)
    skip_aggregator = len(plan.steps) == 1

    def make_worker(step: PlanStep):
        async def worker(state: OrchestratorState) -> dict[str, Any]:
            return await execute_worker_step(
                step=step, agent=agents[step.id], state=state,
                bundle=bundle, factory=factory, manager=manager, run_id=run_id,
                is_terminal_step=step.id in terminal_ids and skip_aggregator)
        return worker

    async def aggregator(state: OrchestratorState) -> dict[str, Any]:
        existing = (state.get("final_answer") or "").strip()
        if existing:
            if bundle.emit is not None:
                await bundle.emit(make_event(
                    EventType.NODE_STARTED, bundle.run_id, node="synthesis"))
                if not bundle.answer_streamed:
                    await bundle.emit(make_event(
                        EventType.ANSWER_DELTA, bundle.run_id, text=existing, done=False))
                    await bundle.emit(make_event(
                        EventType.ANSWER_DELTA, bundle.run_id, text="", done=True))
                await bundle.emit(make_event(
                    EventType.NODE_FINISHED, bundle.run_id, node="synthesis",
                    summary=existing[:300]))
            return {"final_answer": existing}
        provider = factory.provider
        goal = state.get("goal", "")
        conversation = state.get("conversation", "")
        step_summaries = state.get("step_outputs", {})
        entity_gaps = synthesis_entity_gaps(goal, step_summaries)
        synth_system, synth_user = compose_synthesis_prompt(
            goal=goal,
            conversation=conversation,
            step_outputs=step_summaries,
            entity_gaps=entity_gaps or None,
        )
        if bundle.emit is not None:
            await bundle.emit(make_event(
                EventType.NODE_STARTED, bundle.run_id, node="synthesis"))
        answer = ""
        try:
            if bundle.emit is not None:
                await bundle.emit(make_event(
                    EventType.LLM_CALL_STARTED, bundle.run_id,
                    node="synthesis",
                    label="Combining specialist results into your answer…",
                    input_preview=synth_user[:6000],
                ))
            polished: list[str] = []
            async for token in provider.generate_stream(
                    synth_system, synth_user, tier="cheap"):
                polished.append(token)
                if token and bundle.emit is not None:
                    await bundle.emit(make_event(
                        EventType.ANSWER_DELTA, bundle.run_id, text=token, done=False))
            if polished:
                answer = "".join(polished)
                bundle.answer_streamed = True
                if bundle.emit is not None:
                    await bundle.emit(make_event(
                        EventType.ANSWER_DELTA, bundle.run_id, text="", done=True))
            await emit_usage(bundle, "synthesis", provider.last_usage(),
                             provider.usage_model(), provider=provider, allow_empty=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("synthesis failed (%s)", exc)
            answer = await provider.generate(synth_system, synth_user, tier="cheap")
            await emit_usage(bundle, "synthesis", provider.last_usage(),
                             provider.usage_model(), provider=provider, allow_empty=True)

        if bundle.emit is not None:
            await bundle.emit(make_event(
                EventType.NODE_FINISHED, bundle.run_id, node="synthesis",
                summary=(answer or "")[:300]))
        return {"final_answer": answer}

    builder = StateGraph(OrchestratorState)
    for step in plan.steps:
        builder.add_node(step.id, make_worker(step))
    builder.add_node("aggregate", aggregator)
    for step in plan.steps:
        if not step.depends_on:
            builder.add_edge(START, step.id)
        else:
            for dep in step.depends_on:
                builder.add_edge(dep, step.id)
    for step_id in terminal_ids:
        builder.add_edge(step_id, "aggregate")
    builder.add_edge("aggregate", END)
    bundle.graph = (builder.compile(checkpointer=checkpointer)
                    if checkpointer is not None else builder.compile())
    return bundle
