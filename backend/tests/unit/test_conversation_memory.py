"""L2 conversation window — budget aligned with GLM input context."""

from __future__ import annotations

from app.core.llm_budgets import conversation_memory_budget_tokens, summarization_trigger_tokens
from app.memory.conversation import ConversationWindow, window_for


def test_conversation_budget_matches_llm_context(monkeypatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "1048576")
    monkeypatch.setenv("SUMMARIZATION_THRESHOLD", "0.80")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    assert conversation_memory_budget_tokens() == 1_048_576
    assert summarization_trigger_tokens() == 838_860

    win = window_for("thread-budget-test")
    assert win.budget == 1_048_576

    get_settings.cache_clear()


def test_summarization_starts_at_eighty_percent(monkeypatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "1000")
    monkeypatch.setenv("SUMMARIZATION_THRESHOLD", "0.80")
    monkeypatch.setenv("SUMMARY_MIN_TURNS_GAP", "1")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    win = ConversationWindow(thread_id="t1", budget=1000)
    win.token_count = 799
    win.turns_since_summary = ["a"]
    assert not win.should_summarize()

    win.token_count = 800
    assert win.should_summarize()

    get_settings.cache_clear()
