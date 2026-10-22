"""Security-boundary regressions that remain fully keyless."""

from __future__ import annotations

import asyncio
import logging

import pytest
from app.core.logging import CorrelationFilter
from app.core.security import require_api_token
from app.core.settings import get_settings
from fastapi import HTTPException


def test_api_token_is_optional_by_default(monkeypatch) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    get_settings.cache_clear()
    asyncio.run(require_api_token(None))


def test_api_token_rejects_missing_or_wrong_value(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "expected-test-token")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as missing:
        asyncio.run(require_api_token(None))
    with pytest.raises(HTTPException) as wrong:
        asyncio.run(require_api_token("wrong"))
    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401
    asyncio.run(require_api_token("expected-test-token"))


def test_chat_rate_limit_rejects_bursts(monkeypatch) -> None:
    from app.core.security import enforce_chat_rate_limit, reset_chat_rate_limit

    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    reset_chat_rate_limit()
    enforce_chat_rate_limit("sess-a")
    enforce_chat_rate_limit("sess-a")
    with pytest.raises(HTTPException) as blocked:
        enforce_chat_rate_limit("sess-a")
    assert blocked.value.status_code == 429
    enforce_chat_rate_limit("sess-b")


def test_logging_filter_redacts_message_and_positional_args() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "authorization=Bearer-secret %s", ("api_key=also-secret",), None)
    CorrelationFilter().filter(record)
    rendered = record.getMessage()
    assert "Bearer-secret" not in rendered
    assert "also-secret" not in rendered
    assert "***" in rendered
