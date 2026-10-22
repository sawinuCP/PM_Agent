"""Chat pipeline — production path (live LLM + MCP)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.llm_budgets import (
    conversation_tail,
    fast_path_conversation_chars,
    session_context_max_chars,
)
from app.memory.runtime import episodic_hints_for_goal
from app.memory.session_context import load_session_context
from app.models import db
from app.models.plan import Plan
from app.models.session import run_write_scope
from app.observability.bus import EventBus
from app.orchestration.planning.planner import PlanningContext, create_plan
from app.orchestration.routing.router import classify, is_smalltalk
from app.orchestration.workflow.clarify import park_clarify, persist_assistant_message

if TYPE_CHECKING:
    from app.orchestration.workflow.executor import Executor

logger = logging.getLogger(__name__)


async def run_turn(
    *,
    run_id: str,
    session_id: str,
    thread_id: str,
    message: str,
    executor: Executor,
    bus: EventBus,
    platform: Any,
    db_session: AsyncSession | None = None,
) -> None:
    provider = platform.factory.provider
    context_view = await load_session_context(session_id, thread_id)
    conversation = context_view.transcript_text(max_chars=session_context_max_chars())
    conversation_router = context_view.for_router()
    conversation_planner = context_view.for_planner()
    conversation_worker = context_view.for_worker()

    await bus.emit_context_snapshot(
        run_id, "goal", conversation=conversation,
        note="User goal plus session transcript.")

    await bus.emit_llm_call_started(
        run_id, node="router", label="Analyzing your question and choosing a path…")
    decision = await classify(message, provider, conversation=conversation_router)
    logger.info("router: run=%s intent=%s path=%s", run_id, decision.intent, decision.path)
    await bus.emit_router_decision(
        run_id, intent=decision.intent, path=decision.path,
        reason=decision.reason, temporal=decision.temporal_sensitivity)
    if decision.llm_used:
        inp, out = provider.io_preview()
        await bus.emit_token_usage(
            run_id, node="router", usage=provider.last_usage(),
            model=provider.usage_model(),
            input_preview=inp, output_preview=out)

    if decision.path == "fast":
        if decision.intent == "clarify" and decision.direct_answer:
            await park_clarify(
                run_id=run_id, session_id=session_id, message=message,
                question=decision.direct_answer.strip(),
                conversation_worker=conversation_worker, bus=bus,
                reason="router", db_session=db_session)
            return
        await _complete_fast_path(
            run_id=run_id, session_id=session_id, message=message,
            conversation=conversation, conversation_router=conversation_router,
            decision=decision, platform=platform, bus=bus,
            db_session=db_session)
        return

    episodic_hints = await episodic_hints_for_goal(message, thread_id=thread_id)
    if episodic_hints:
        await bus.emit_context_snapshot(
            run_id, "planner",
            episodic_hints=episodic_hints,
            note="Structure-only hints from similar prior runs; live tools remain authoritative.",
        )

    context = PlanningContext(
        thread_id=thread_id,
        goal=message,
        conversation=conversation_planner,
        conversation_memory=context_view.planner_payload(),
        temporal_sensitivity=decision.temporal_sensitivity,
    )
    await bus.emit_llm_call_started(
        run_id, node="planner", label="Building a step-by-step plan for specialists…")
    plan, planner_llm_used = await create_plan(
        context, provider, platform.registry, platform.manager,
        hints=episodic_hints or None)
    if planner_llm_used:
        inp, out = provider.io_preview()
        await bus.emit_token_usage(
            run_id, node="planner", usage=provider.last_usage(),
            model=provider.usage_model(), allow_empty=True,
            input_preview=inp, output_preview=out)

    if plan is not None:
        if plan.clarify_question and plan.clarify_question.strip():
            await _park_planner_clarify(
                run_id=run_id, session_id=session_id, message=message,
                plan=plan, conversation=conversation, conversation_worker=conversation_worker,
                bus=bus, db_session=db_session)
            return
        await bus.emit_plan_created(run_id, plan.model_dump(), plan.uses_episodic_hint)
        await db.update_run_record(
            run_id, session=db_session, plan=plan.model_dump())

    unavailable = platform.manager.unavailable_servers()
    if unavailable:
        logger.warning(
            "run=%s skipping execution — MCP unavailable: %s", run_id, unavailable)
        await executor.execute_mcp_unavailable(
            run_id=run_id, session_id=session_id, thread_id=thread_id,
            goal=message, unavailable=unavailable,
            conversation=conversation_worker,
            session_context=context_view.worker_payload(),
            db_session=db_session)
        return

    await executor.execute(
        run_id=run_id, session_id=session_id, thread_id=thread_id,
        goal=message, plan=plan, conversation=conversation_worker,
        session_context=context_view.worker_payload(),
        db_session=db_session)


async def _complete_fast_path(
    *, run_id: str, session_id: str, message: str,
    conversation: str, conversation_router: str,
    decision: Any, platform: Any, bus: EventBus,
    db_session: AsyncSession | None = None,
) -> None:
    provider = platform.factory.provider
    await bus.emit_llm_call_started(
        run_id, node="fast", label="Drafting a direct answer…")
    answer, generated = await fast_path_answer(
        message, platform, direct_answer=decision.direct_answer,
        conversation=conversation_router)
    if generated:
        inp, out = provider.io_preview()
        await bus.emit_token_usage(
            run_id, node="fast", usage=provider.last_usage(),
            model=provider.usage_model(),
            input_preview=inp, output_preview=out)
    usage = {**bus.usage_totals(run_id), "path": "fast"}
    async with run_write_scope(db_session) as sess:
        await db.update_run_record(
            run_id, session=sess, status="completed", result_summary=answer,
            token_usage=usage, cost_usd=usage["cost_usd"],
            finished_at=utcnow())
        await db.add_session_cost(session_id, float(usage["cost_usd"]), session=sess)
        await persist_assistant_message(session_id, run_id, answer, session=sess)
    await bus.emit_run_finished(run_id, answer, usage)


async def fast_path_answer(
    message: str, platform: Any, *,
    direct_answer: str | None = None, conversation: str = "",
) -> tuple[str, bool]:
    if direct_answer:
        return direct_answer, False
    if is_smalltalk(message):
        return (
            "Hello! I'm Azend — I coordinate specialist agents to answer questions "
            "using connected tools (Jira, sprints, analytics). What would you like to know?",
            False,
        )
    provider = platform.factory.provider
    role = ""
    try:
        role = (platform.registry.get("conversational").system_prompt or "").strip()
    except Exception:  # noqa: BLE001
        role = ""
    user_block = message
    if conversation.strip():
        user_block = (
            f"Session conversation:\n"
            f"{conversation_tail(conversation, fast_path_conversation_chars())}\n\n"
            f"Latest user message: {message}"
        )
    text = await provider.generate(
        system=role or "You are a helpful assistant.",
        user=user_block,
        tier="cheap")
    return text, True


async def _park_planner_clarify(
    *, run_id: str, session_id: str, message: str,
    plan: Plan, conversation: str, conversation_worker: str, bus: EventBus,
    db_session: AsyncSession | None = None,
) -> None:
    await park_clarify(
        run_id=run_id, session_id=session_id, message=message,
        question=plan.clarify_question.strip(),  # type: ignore[union-attr]
        conversation_worker=conversation_worker, bus=bus,
        reason="planner", plan_dump=plan.model_dump(),
        db_session=db_session)
