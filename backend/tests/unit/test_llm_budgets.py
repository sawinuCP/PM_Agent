"""LLM context budgets scale with ``llm_context_tokens``."""

from __future__ import annotations

from app.core import llm_budgets


def test_glm_context_budgets_at_one_million_tokens(monkeypatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "1048576")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    # 10% of 1M tokens * 3 chars/token
    assert llm_budgets.session_context_max_chars() == 314_572
    assert llm_budgets.router_conversation_chars() == 3_145
    assert llm_budgets.worker_conversation_chars() == 15_728

    get_settings.cache_clear()
