"""Embedding-provider selection and deterministic fallback tests."""

from __future__ import annotations

import pytest
from app.core.errors import ConfigError
from app.core.settings import get_settings
from app.memory import embeddings


def test_hash_embeddings_are_deterministic_and_dimensioned(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "hash")
    monkeypatch.setenv("EMBEDDINGS_DIM", "12")
    get_settings.cache_clear()
    first = embeddings.embed_text("same text", input_type="query")
    second = embeddings.embed_text("same text", input_type="document")
    assert first == second
    assert len(first) == 12


def test_auto_selects_voyage_only_when_key_is_present(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "auto")
    get_settings.cache_clear()
    settings = get_settings()
    # Empty process env is ignored in favor of backend/.env — pin the field
    # so this test does not depend on whether a developer key is present.
    monkeypatch.setattr(settings, "voyage_api_key", None)
    assert embeddings.active_provider() == "hash"
    monkeypatch.setattr(settings, "voyage_api_key", "test-key-not-used")
    assert embeddings.active_provider() == "voyage"


def test_explicit_voyage_requires_key(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "voyage")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "voyage_api_key", None)
    with pytest.raises(ConfigError, match="requires VOYAGE_API_KEY"):
        embeddings.embed_text("probe", input_type="query")


def test_hash_embed_records_inspector_telemetry(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "hash")
    monkeypatch.setenv("EMBEDDINGS_DIM", "12")
    get_settings.cache_clear()
    with embeddings.capture_embeddings() as calls:
        embeddings.embed_text("sprint velocity forecast", input_type="query")
    assert len(calls) == 1
    call = calls[0]
    assert call.provider == "hash"
    assert call.model == "hash"
    assert call.input_type == "query"
    assert call.tokens > 0
    assert call.ms >= 0
    assert call.cost_usd == 0.0
    assert call.cached is False
