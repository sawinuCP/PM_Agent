"""Memory-layer extraction and session context contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SemanticFacts(BaseModel):
    """Post-run memory extraction contract."""

    facts: list[str] = Field(default_factory=list)


class SessionContextStats(BaseModel):
    """Per-session context fill relative to the GLM input window."""

    transcript_tokens: int
    memory_window_tokens: int
    effective_tokens: int
    budget_tokens: int
    threshold_tokens: int
    threshold_pct: float
    utilization_pct: float
    stored_message_tokens: int
    message_count: int
    has_summary: bool
    summarization_pending: bool
    session_context_chars: int
    session_context_char_limit: int
