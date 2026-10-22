"""Session context stats API."""

from __future__ import annotations

import pytest

from app.memory.session_stats import get_session_context_stats


@pytest.mark.asyncio
async def test_session_context_stats_empty_session(monkeypatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "1048576")
    monkeypatch.setenv("SUMMARIZATION_THRESHOLD", "0.80")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    from app.models import db

    await db.init_schema()
    record = await db.create_session_record("stats-test", "demo")
    stats = await get_session_context_stats(record.id)
    assert stats is not None
    assert stats.budget_tokens == 1_048_576
    assert stats.threshold_tokens == 838_860
    assert stats.effective_tokens == 0
    assert stats.utilization_pct == 0.0
    assert stats.message_count == 0

    get_settings.cache_clear()
