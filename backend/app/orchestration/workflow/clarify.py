"""HITL clarify — goal enrichment, session lookup, and run parking."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tokens import count_tokens
from app.models import db
from app.models.session import run_write_scope
from app.observability.bus import EventBus
from app.orchestration.hitl import clarify_interrupt_metadata


def enrich_goal_with_clarification(goal: str, answer: str) -> str:
    base = (goal or "").strip()
    reply = (answer or "").strip()
    if not reply:
        return base
    return f"{base}\n\nUser clarification: {reply}"


def user_already_clarified(goal: str) -> bool:
    return "User clarification:" in (goal or "")


def coerce_plan_after_clarify(goal: str, thread_id: str, plan: "Plan") -> "Plan":
    """After the user answered clarify, never park again — execute best-effort."""
    from app.models.plan import Plan, PlanStep

    plan.clarify_question = None
    if plan.steps:
        return plan
    task = goal.strip()
    return Plan(
        thread_id=thread_id,
        goal=task,
        steps=[PlanStep(id="s1", agent="jira-analyst", task=task, depends_on=[])],
        synthesis=plan.synthesis or "Answer the user's question from fetched Jira data.",
        uses_episodic_hint=plan.uses_episodic_hint,
        reasoning=plan.reasoning
        or ["User clarified scope; jira-analyst fetches data for the stated goal."],
    )


async def find_pending_clarify_run(session_id: str):
    runs = await db.list_runs_for_session(session_id, limit=8)
    for run in runs:
        if run.status == "awaiting_clarification":
            return run
    return None


async def persist_assistant_message(
    session_id: str,
    run_id: str,
    answer: str,
    *,
    session: AsyncSession | None = None,
) -> None:
    if answer:
        await db.add_message(
            session_id, "assistant", {"text": answer},
            run_id=run_id, token_count=count_tokens(answer),
            session=session,
        )


async def park_clarify(
    *,
    run_id: str,
    session_id: str,
    message: str,
    question: str,
    conversation_worker: str,
    bus: EventBus,
    reason: str,
    plan_dump: dict | None = None,
    db_session: AsyncSession | None = None,
    replan_count: int = 0,
) -> None:
    """Park a run until the user answers a clarifying question (router or planner)."""
    answer = question.strip()
    payload = dict(plan_dump or {})
    payload["_interrupt"] = clarify_interrupt_metadata(
        goal=message,
        question=answer,
        conversation=conversation_worker,
        replan_count=replan_count,
    )
    async with run_write_scope(db_session) as sess:
        await db.update_run_record(
            run_id, session=sess, status="awaiting_clarification", plan=payload,
            result_summary=answer)
        await persist_assistant_message(session_id, run_id, answer, session=sess)
    await bus.emit_clarify_required(run_id, question=answer, reason=reason)
