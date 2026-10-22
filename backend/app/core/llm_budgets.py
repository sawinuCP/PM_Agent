"""Character budgets derived from ``llm_context_tokens`` (GLM window).

Centralizes conversation truncation so raising the model context in ``.env``
scales session memory and per-node prompts together.
"""

from __future__ import annotations

from app.core.settings import get_settings

# Conservative chars/token for mixed markdown + JSON prompts.
_CHARS_PER_TOKEN = 3


def _context_chars() -> int:
    settings = get_settings()
    return max(4096, int(settings.llm_context_tokens) * _CHARS_PER_TOKEN)


def _slice_chars(fraction: float, *, floor: int) -> int:
    return max(floor, int(_context_chars() * fraction))


def session_context_max_chars() -> int:
    """Full session transcript loaded for each run."""
    return _slice_chars(0.10, floor=4500)


def router_conversation_chars() -> int:
    return _slice_chars(0.001, floor=800)


def planner_conversation_chars() -> int:
    return _slice_chars(0.002, floor=1500)


def worker_conversation_chars() -> int:
    return _slice_chars(0.005, floor=2500)


def worker_context_json_chars() -> int:
    return _slice_chars(0.003, floor=1500)


def synthesis_conversation_chars() -> int:
    return _slice_chars(0.004, floor=2000)


def synthesis_evidence_chars() -> int:
    """Budget for synthesis EVIDENCE_JSON — parallel multi-step runs need headroom."""
    return _slice_chars(0.015, floor=12000)


def fast_path_conversation_chars() -> int:
    return _slice_chars(0.006, floor=3000)


def conversation_memory_budget_tokens() -> int:
    """L2 rolling-summary window — aligned with GLM max input context."""
    return max(4096, int(get_settings().llm_context_tokens))


def conversation_tail(text: str, max_chars: int) -> str:
    """Recent transcript tail — follow-ups live at the end of the thread."""
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    return body[-max_chars:]


def summarization_trigger_tokens() -> int:
    """Token count at which post-turn rolling summarization starts."""
    settings = get_settings()
    return int(conversation_memory_budget_tokens() * settings.summarization_threshold)
