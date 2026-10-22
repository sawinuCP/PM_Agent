"""Orchestration-facing memory hooks for planning reads and post-run writes.

Memory is always best-effort: failures are logged and never block user-facing runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.memory import conversation, episodic, semantic
from app.models import db
from app.models.plan import Plan

logger = logging.getLogger(__name__)


async def episodic_hints_for_goal(
    goal: str, *, thread_id: str | None = None, top_k: int = 2,
) -> list[dict[str, Any]]:
    """L3 episodic hints for the planner (plan shape only).

    Scoped to ``thread_id`` when provided so a new chat session does not inherit
    hints from unrelated prior sessions in the database.
    """
    try:
        return await episodic.hints_for_goal(goal, thread_id=thread_id, top_k=top_k)
    except Exception:  # noqa: BLE001 — memory must never block planning
        logger.warning("episodic hint retrieval failed", exc_info=True)
        return []


async def semantic_notes_for_agent(role: str, goal: str, *, top_k: int = 3) -> list[str]:
    """L4 durable facts retrieved at agent provision time."""
    try:
        facts = await semantic.relevant_facts_for(goal, role, top_k=top_k)
        return [fact.strip() for fact in facts if isinstance(fact, str) and fact.strip()]
    except Exception:  # noqa: BLE001
        logger.warning("semantic retrieval failed role=%s", role, exc_info=True)
        return []


def mark_plan_hint_usage(plan: Plan, hints: list[dict[str, Any]] | None) -> Plan:
    """Platform-owned flag — never trust the planner LLM to set this field."""
    return plan.model_copy(update={"uses_episodic_hint": bool(hints)})


@dataclass(frozen=True)
class PostRunContext:
    run_id: str
    thread_id: str
    session_id: str
    goal: str
    plan: Plan | None
    answer: str
    outcome: Literal["success", "failure"]
    cost_usd: float
    duration_s: float


async def persist_post_run(ctx: PostRunContext) -> None:
    """L2 conversation window + L4 semantic extraction (background-safe)."""
    await _persist_l2_conversation(ctx)
    if ctx.outcome != "success" or ctx.plan is None:
        return
    await _persist_l4_semantic(ctx)


async def commit_episodic_record(ctx: PostRunContext) -> bool:
    """L3 episodic write — await before run_finished so the next turn sees hints."""
    if ctx.outcome != "success" or ctx.plan is None:
        return False
    try:
        await _persist_l3_episodic(ctx)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("L3 episodic record failed run=%s", ctx.run_id)
        return False


async def _persist_l2_conversation(ctx: PostRunContext) -> None:
    try:
        win = conversation.window_for(ctx.thread_id)
        win.add_turn(f"user: {ctx.goal}")
        win.add_turn(f"assistant: {ctx.answer[:500]}")
        await conversation.maybe_summarize(win)
        await conversation.persist_window(ctx.session_id, win)
    except Exception:  # noqa: BLE001
        logger.exception("L2 conversation persist failed run=%s", ctx.run_id)


async def _persist_l3_episodic(ctx: PostRunContext) -> None:
    assert ctx.plan is not None
    try:
        await episodic.record_run(
            thread_id=ctx.thread_id,
            goal_text=ctx.goal,
            plan=ctx.plan.model_dump(),
            outcome="success",
            cost_usd=ctx.cost_usd,
            duration_s=ctx.duration_s,
        )
    except Exception:  # noqa: BLE001
        logger.exception("L3 episodic record failed run=%s", ctx.run_id)


async def _persist_l4_semantic(ctx: PostRunContext) -> None:
    try:
        summaries = await _step_summaries(ctx.run_id, ctx.plan)
        await semantic.extract_and_store(
            goal=ctx.goal,
            step_summaries=summaries,
            run_id=ctx.run_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("L4 semantic extraction failed run=%s", ctx.run_id)


async def _step_summaries(run_id: str, plan: Plan | None) -> list[str]:
    """Planner tasks plus node_finished summaries for semantic extraction."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        text = line.strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)

    if plan is not None:
        for step in plan.steps:
            add(f"{step.agent}: {step.task}")

    try:
        for trace in await db.list_traces(run_id):
            if trace.event_type != "node_finished":
                continue
            payload = trace.payload if isinstance(trace.payload, dict) else {}
            node = str(payload.get("node") or payload.get("step_id") or "step")
            summary = str(payload.get("summary") or "").strip()
            if summary:
                add(f"{node}: {summary}")
    except Exception:  # noqa: BLE001
        logger.debug("node summaries unavailable run=%s", run_id, exc_info=True)

    return ordered[:6]
