"""Session context utilization — tokens loaded vs GLM input budget."""

from __future__ import annotations

from app.ai.tokens import count_tokens
from app.core.llm_budgets import (
    conversation_memory_budget_tokens,
    session_context_max_chars,
    summarization_trigger_tokens,
)
from app.core.settings import get_settings
from app.memory import conversation
from app.memory.session_context import build_session_context
from app.models import db
from app.models.memory import SessionContextStats


async def get_session_context_stats(session_id: str) -> SessionContextStats | None:
    """Token fill for the active chat session (transcript + L2 memory window)."""
    record = await db.get_session_record(session_id)
    if record is None:
        return None

    settings = get_settings()
    budget = conversation_memory_budget_tokens()
    threshold_tokens = summarization_trigger_tokens()
    char_limit = session_context_max_chars()

    transcript = await build_session_context(session_id, record.thread_id)
    transcript_tokens = count_tokens(transcript)
    mem = conversation.memory_stats_for(record.thread_id)
    persisted_tokens = int(record.memory_window_tokens or 0)
    memory_window_tokens = max(int(mem["memory_window_tokens"]), persisted_tokens)
    has_summary = bool(mem["has_summary"]) or bool((record.conversation_summary or "").strip())

    messages = await db.list_messages(session_id, limit=200)
    stored_message_tokens = sum(int(m.token_count or 0) for m in messages)

    effective = max(transcript_tokens, memory_window_tokens)
    utilization = (effective / budget * 100.0) if budget else 0.0

    return SessionContextStats(
        transcript_tokens=transcript_tokens,
        memory_window_tokens=memory_window_tokens,
        effective_tokens=effective,
        budget_tokens=budget,
        threshold_tokens=threshold_tokens,
        threshold_pct=settings.summarization_threshold,
        utilization_pct=round(utilization, 2),
        stored_message_tokens=stored_message_tokens,
        message_count=len(messages),
        has_summary=has_summary,
        summarization_pending=bool(mem["summarization_pending"]),
        session_context_chars=len(transcript),
        session_context_char_limit=char_limit,
    )
