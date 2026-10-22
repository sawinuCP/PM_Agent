"""Run executor — graph invoke, status updates, and assistant persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.factory import AgentFactory
from app.ai.prompts import FALLBACK_ANSWER_SYSTEM, MCP_UNAVAILABLE_ANSWER_SYSTEM
from app.ai.tokens import count_tokens
from app.core.clock import utcnow
from app.core.logging import bind_run_context
from app.core.settings import get_settings
from app.memory.runtime import (
    PostRunContext,
    commit_episodic_record,
    episodic_hints_for_goal,
    persist_post_run,
)
from app.memory.session_context import load_session_context
from app.models import db
from app.models.plan import Plan
from app.models.session import run_write_scope
from app.observability.bus import EventBus
from app.orchestration.graph.builder import GraphBundle, build_graph
from app.orchestration.graph.worker import emit_usage
from app.orchestration.planning.planner import PlanningContext, create_plan
from app.orchestration.workflow.clarify import (
    coerce_plan_after_clarify,
    enrich_goal_with_clarification,
    park_clarify,
    user_already_clarified,
)
from app.orchestration.planning.plan_deps import normalize_join_dependencies
from app.orchestration.planning.plan_tools import sanitize_plan, validate_plan

logger = logging.getLogger(__name__)


async def drain_post_runs() -> None:
    """No-op — post-run tasks are fire-and-forget in the slim executor."""


@dataclass
class ActiveRun:
    run_id: str
    thread_id: str
    session_id: str
    goal: str
    conversation: str = ""
    session_context: dict[str, Any] = field(default_factory=dict)
    plan: Plan | None = None
    bundle: GraphBundle | None = None
    status: str = "running"
    started: float = field(default_factory=time.monotonic)
    replan_count: int = 0


class Executor:
    def __init__(self, factory: AgentFactory, bus: EventBus,
                 checkpointer: Any = None) -> None:
        self._factory = factory
        self._manager = factory._manager
        self._bus = bus
        self._checkpointer = checkpointer
        self._active: dict[str, ActiveRun] = {}

    async def execute(self, *, run_id: str, session_id: str, thread_id: str,
                      goal: str, plan: Plan | None,
                      conversation: str = "",
                      session_context: dict[str, Any] | None = None,
                      db_session: AsyncSession | None = None) -> ActiveRun:
        active = ActiveRun(
            run_id=run_id, thread_id=thread_id, session_id=session_id,
            goal=goal, conversation=conversation, plan=plan,
            session_context=session_context or {})
        self._active[run_id] = active
        bind_run_context(run_id=run_id, thread_id=thread_id)
        started = time.monotonic()
        try:
            if plan is None:
                await self._degraded_answer(active, db_session=db_session)
                return active
            result = await self._invoke_plan(active, plan)
            answer = (result or {}).get("final_answer", "")
            await self._complete_run(active, plan, answer, started, db_session=db_session)
        except Exception as exc:  # noqa: BLE001
            logger.exception("run %s failed", run_id)
            safe = getattr(exc, "safe_message", "The request could not be completed.")
            await self._bus.emit_run_failed(run_id, safe)
            async with run_write_scope(db_session) as sess:
                await db.update_run_record(
                    run_id, session=sess, status="failed", error=safe,
                    finished_at=utcnow())
            active.status = "failed"
        finally:
            if active.status not in ("awaiting_clarification", "awaiting_approval"):
                self._active.pop(run_id, None)
        return active

    async def resume_with_clarification(
        self, run_id: str, answer: str, *, platform: Any,
        db_session: AsyncSession | None = None,
    ) -> ActiveRun | None:
        run = await db.get_run_record(run_id, session=db_session)
        if run is None or run.status != "awaiting_clarification":
            return None
        interrupt = (run.plan or {}).get("_interrupt") if isinstance(run.plan, dict) else {}
        replan_count = int((interrupt or {}).get("replan_count", 0)) + 1
        if replan_count > get_settings().max_clarify_replans:
            return None
        base_goal = str((interrupt or {}).get("goal") or run.goal_text)
        enriched = enrich_goal_with_clarification(base_goal, answer)
        context_view = await load_session_context(run.session_id, run.thread_id)
        active = ActiveRun(
            run_id=run_id, thread_id=run.thread_id, session_id=run.session_id,
            goal=enriched, conversation=context_view.for_worker(),
            session_context=context_view.worker_payload(),
            replan_count=replan_count)
        self._active[run_id] = active
        try:
            async with run_write_scope(db_session) as sess:
                await db.update_run_record(
                    run_id, session=sess, status="running", goal_text=enriched)
            episodic_hints = await episodic_hints_for_goal(
                enriched, thread_id=run.thread_id)
            plan, planner_llm_used = await create_plan(
                PlanningContext(
                    thread_id=run.thread_id, goal=enriched,
                    conversation=context_view.for_planner(),
                    conversation_memory=context_view.planner_payload(),
                ),
                platform.factory.provider, platform.registry, platform.manager,
                hints=episodic_hints or None)
            if planner_llm_used:
                inp, out = platform.factory.provider.io_preview()
                await self._bus.emit_token_usage(
                    run_id, node="planner",
                    usage=platform.factory.provider.last_usage(),
                    model=platform.factory.provider.usage_model(),
                    allow_empty=True,
                    input_preview=inp, output_preview=out)
            if plan is None:
                await self._degraded_answer(active, db_session=db_session)
                return active
            if plan.clarify_question and plan.clarify_question.strip():
                if replan_count >= 1 and user_already_clarified(enriched):
                    logger.info(
                        "suppressing repeat clarify after user answer run=%s", run_id)
                    allowed = {
                        entry.name: list(entry.tools)
                        for entry in platform.registry.capability_catalog()
                    }
                    catalog_tools = {
                        item["name"]
                        for catalog in platform.manager.tool_catalog().values()
                        for item in catalog
                    }
                    plan = coerce_plan_after_clarify(enriched, run.thread_id, plan)
                    sanitize_plan(
                        plan, allowed_by_agent=allowed, catalog_tools=catalog_tools)
                    normalize_join_dependencies(plan)
                    validate_plan(
                        plan, allowed_by_agent=allowed, catalog_tools=catalog_tools)
                else:
                    context_view = await load_session_context(
                        run.session_id, run.thread_id)
                    await park_clarify(
                        run_id=run_id,
                        session_id=run.session_id,
                        message=base_goal,
                        question=plan.clarify_question.strip(),
                        conversation_worker=context_view.for_worker(),
                        bus=self._bus,
                        reason="planner",
                        plan_dump=plan.model_dump(),
                        db_session=db_session,
                        replan_count=replan_count,
                    )
                    active.status = "awaiting_clarification"
                    return active
            result = await self._invoke_plan(active, plan)
            await self._complete_run(
                active, plan, (result or {}).get("final_answer", ""),
                time.monotonic(), db_session=db_session,
            )
        except Exception:  # noqa: BLE001
            logger.exception("clarify resume failed run=%s", run_id)
            active.status = "failed"
        finally:
            self._active.pop(run_id, None)
        return active

    def resolve_approval(self, run_id: str, approved: bool) -> bool:
        active = self._active.get(run_id)
        if active is None or active.bundle is None or active.bundle.pending_tool is None:
            return False
        active.bundle.resolve_approval(approved)
        return True

    async def _invoke_plan(
        self,
        active: ActiveRun,
        plan: Plan,
    ) -> dict[str, Any]:
        bundle = build_graph(plan, self._factory, self._manager,
                             run_id=active.run_id, thread_id=active.thread_id,
                             checkpointer=self._checkpointer)
        active.bundle = bundle
        bundle.emit = self._bus.emit
        bundle.on_awaiting_approval = lambda: setattr(active, "status", "awaiting_approval")
        for step in plan.steps:
            bundle.agents[step.id] = await self._factory.spawn(
                step, run_id=active.run_id, thread_id=active.thread_id,
                goal=active.goal, emit=self._bus.emit)
        state: dict[str, Any] = {
            "run_id": active.run_id, "thread_id": active.thread_id,
            "goal": active.goal, "conversation": active.conversation,
            "session_context": active.session_context,
            "plan": plan.model_dump(), "step_outputs": {},
            "tool_result_refs": {}, "final_answer": "",
        }
        config: dict[str, Any] = {
            "configurable": {"thread_id": f"{active.thread_id}:{active.run_id}"},
        }
        return await bundle.graph.ainvoke(state, config=config)

    async def _complete_run(
        self,
        active: ActiveRun,
        plan: Plan,
        answer: str,
        started: float,
        *,
        db_session: AsyncSession | None = None,
    ) -> None:
        usage = self._bus.usage_totals(active.run_id)
        async with run_write_scope(db_session) as sess:
            await self._persist_answer(active, answer, session=sess)
            await db.update_run_record(
                active.run_id, session=sess, status="completed",
                plan=plan.model_dump(), result_summary=answer, token_usage=usage,
                cost_usd=usage["cost_usd"], finished_at=utcnow())
            await db.add_session_cost(
                active.session_id, float(usage["cost_usd"]), session=sess)
        post_ctx = self._post_run_context(active, plan, answer, started, usage)
        await commit_episodic_record(post_ctx)
        streamed = bool(active.bundle and active.bundle.answer_streamed)
        await self._bus.emit_run_finished(
            active.run_id, answer, usage, stream=not streamed)
        active.status = "completed"
        asyncio.create_task(self._post_run(post_ctx))

    async def _degraded_answer(
        self,
        active: ActiveRun,
        *,
        db_session: AsyncSession | None = None,
    ) -> None:
        provider = self._factory.provider
        answer = await provider.generate(
            FALLBACK_ANSWER_SYSTEM,
            f"User goal: {active.goal}\nRespond per the instructions above.",
            tier="cheap")
        dummy = GraphBundle(graph=None, agents={}, approved_tools=set())
        dummy.emit = self._bus.emit
        dummy.run_id = active.run_id
        await emit_usage(dummy, "fallback", provider.last_usage(), provider.usage_model(),
                         provider=provider)
        usage = {**self._bus.usage_totals(active.run_id), "degraded": True}
        async with run_write_scope(db_session) as sess:
            await db.update_run_record(
                active.run_id, session=sess, status="completed",
                result_summary=answer, token_usage=usage,
                cost_usd=usage["cost_usd"], finished_at=utcnow())
            await self._persist_answer(active, answer, session=sess)
        await self._bus.emit_run_finished(active.run_id, answer, usage)
        active.status = "completed"

    async def execute_mcp_unavailable(
        self, *, run_id: str, session_id: str, thread_id: str,
        goal: str, unavailable: list[str],
        conversation: str = "",
        session_context: dict[str, Any] | None = None,
        db_session: AsyncSession | None = None,
    ) -> ActiveRun:
        """Graceful completion when required MCP servers are down."""
        active = ActiveRun(
            run_id=run_id, thread_id=thread_id, session_id=session_id,
            goal=goal, conversation=conversation,
            session_context=session_context or {})
        provider = self._factory.provider
        servers = ", ".join(unavailable) or "MCP services"
        answer = await provider.generate(
            MCP_UNAVAILABLE_ANSWER_SYSTEM,
            f"User goal: {goal}\nUnavailable services: {servers}\n"
            "Respond per the instructions above.",
            tier="cheap")
        dummy = GraphBundle(graph=None, agents={}, approved_tools=set())
        dummy.emit = self._bus.emit
        dummy.run_id = run_id
        await emit_usage(dummy, "mcp_unavailable", provider.last_usage(),
                         provider.usage_model(), provider=provider)
        usage = {
            **self._bus.usage_totals(run_id),
            "degraded": True,
            "mcp_unavailable": unavailable,
        }
        async with run_write_scope(db_session) as sess:
            await db.update_run_record(
                run_id, session=sess, status="completed", result_summary=answer,
                token_usage=usage, cost_usd=usage["cost_usd"], finished_at=utcnow())
            await self._persist_answer(active, answer, session=sess)
        await self._bus.emit_run_finished(run_id, answer, usage)
        active.status = "completed"
        return active

    async def _persist_answer(
        self,
        active: ActiveRun,
        answer: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        if answer:
            await db.add_message(
                active.session_id, "assistant", {"text": answer},
                run_id=active.run_id, token_count=count_tokens(answer),
                session=session)

    def _post_run_context(
        self, active: ActiveRun, plan: Plan | None, answer: str,
        started: float, usage: dict[str, Any],
    ) -> PostRunContext:
        return PostRunContext(
            run_id=active.run_id,
            thread_id=active.thread_id,
            session_id=active.session_id,
            goal=active.goal,
            plan=plan,
            answer=answer,
            outcome="success",
            cost_usd=float(usage.get("cost_usd", 0.0)),
            duration_s=max(0.0, time.monotonic() - started),
        )

    async def _post_run(self, ctx: PostRunContext) -> None:
        await persist_post_run(ctx)

    @property
    def pending_run_for_tool(self) -> str | None:
        for run_id, active in self._active.items():
            if active.bundle and active.bundle.pending_tool:
                return run_id
        return None
