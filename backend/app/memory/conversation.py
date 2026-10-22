"""Conversation memory (L2): token accounting and 80/30 hysteresis summarizer.

Summarize when a thread's running count crosses 80% of ``llm_context_tokens``,
reset to ~30% (hysteresis), with a min-turns anti-thrash floor. Summarization
is scheduled post-turn by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ai.provider import get_provider
from app.ai.tokens import count_tokens
from app.core.llm_budgets import conversation_memory_budget_tokens
from app.core.settings import get_settings
from app.models import db

logger = logging.getLogger(__name__)


@dataclass
class ConversationWindow:
    """Rolling per-thread conversation state with hysteresis bookkeeping."""

    thread_id: str
    budget: int
    summary: str = ""
    turns_since_summary: list[str] = field(default_factory=list)
    token_count: int = 0

    def add_turn(self, text: str) -> None:
        self.token_count += count_tokens(text)
        self.turns_since_summary.append(text)

    def should_summarize(self) -> bool:
        settings = get_settings()
        if self.token_count >= self.budget * settings.summarization_threshold:
            if len(self.turns_since_summary) >= settings.summary_min_turns_gap:
                return True
            logger.debug("threshold hit but anti-thrash floor not reached (thread=%s)",
                         self.thread_id)
        return False

    def apply_summary(self, new_summary: str) -> None:
        """Replace history with the rolling summary; reset to ~30% (hysteresis)."""
        settings = get_settings()
        self.summary = new_summary
        self.turns_since_summary.clear()
        self.token_count = int(self.budget * settings.summarization_reset)

    def render(self) -> str:
        """Context for the planner: summary + recent verbatim turns."""
        parts = []
        if self.summary:
            parts.append(f"[earlier conversation summary] {self.summary}")
        parts.extend(self.turns_since_summary[-6:])
        return "\n".join(parts)


_WINDOWS: dict[str, ConversationWindow] = {}


def window_for(thread_id: str, *, budget: int | None = None) -> ConversationWindow:
    resolved = budget or conversation_memory_budget_tokens()
    win = _WINDOWS.get(thread_id)
    if win is None:
        win = ConversationWindow(thread_id=thread_id, budget=resolved)
        _WINDOWS[thread_id] = win
    elif budget is None:
        win.budget = resolved
    return win


def summary_for(thread_id: str) -> str:
    """Rolling summary for a thread, if summarization has run."""
    win = _WINDOWS.get(thread_id)
    return win.summary if win else ""


def render_for(thread_id: str) -> str:
    """Planner/router context for a thread, or empty if none exists yet."""
    win = _WINDOWS.get(thread_id)
    return win.render() if win else ""


def memory_stats_for(thread_id: str) -> dict[str, int | bool]:
    """In-memory L2 window stats without creating a new window."""
    win = _WINDOWS.get(thread_id)
    if win is None:
        return {
            "memory_window_tokens": 0,
            "has_summary": False,
            "summarization_pending": False,
        }
    return {
        "memory_window_tokens": win.token_count,
        "has_summary": bool(win.summary),
        "summarization_pending": win.should_summarize(),
    }


def hydrate_window(
    win: ConversationWindow,
    summary: str,
    token_count: int,
) -> None:
    """Sync in-memory window from persisted session state (e.g. after restart)."""
    if summary and not win.summary:
        win.summary = summary
    if token_count > win.token_count:
        win.token_count = token_count


async def persist_window(session_id: str, win: ConversationWindow) -> None:
    """Write rolling summary and token accounting back to the session row."""
    await db.update_session_record(
        session_id,
        conversation_summary=win.summary or None,
        memory_window_tokens=win.token_count,
    )


async def maybe_summarize(win: ConversationWindow) -> bool:
    """Rolling incremental summary: new = summarize(old + turns since last).

    Called after a response streams. Degrades gracefully: on failure
    the window keeps pre-summary state (larger but valid).
    """
    if not win.should_summarize():
        return False
    provider = get_provider()
    transcript = "\n".join(win.turns_since_summary)
    try:
        summary = await provider.generate(
            system="You compress conversation history faithfully and tersely.",
            user=(f"Existing summary:\n{win.summary or '(none)'}\n\n"
                  f"New turns:\n{transcript}\n\nProduce the updated rolling summary."),
            tier="cheap",
        )
    except Exception as exc:  # noqa: BLE001 - graceful degradation
        logger.warning("summarization failed for thread=%s: %s", win.thread_id, exc)
        return False
    win.apply_summary(summary.strip())
    reset_pct = int(get_settings().summarization_reset * 100)
    logger.info(
        "conversation summarized (thread=%s, tokens reset to ~%d%%)",
        win.thread_id, reset_pct)
    return True
