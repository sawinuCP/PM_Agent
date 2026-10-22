"""Token / cost events must be emitted for mock and real providers."""

import asyncio

import pytest

from app.ai.cost import Usage, cost_of, estimate_usage
from app.ai.provider import MockLLM, resolve_llm_provider
from app.core.settings import Settings
from app.observability.bus import EventBus
from app.observability.events import EventType, make_event


def test_estimate_usage_counts_prompt_and_completion() -> None:
    usage = estimate_usage("hello world " * 20, "thanks")
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def test_mock_llm_returns_nonzero_usage() -> None:
    provider = MockLLM()
    text = asyncio.run(provider.generate("You are a helper.", "hello there"))
    usage = provider.last_usage()
    assert text
    assert usage.total_tokens > 0
    assert cost_of(usage, "mock") >= 0


def test_bus_accumulates_token_usage() -> None:
    async def run() -> dict:
        bus = EventBus()
        await bus.emit_token_usage(
            "run-1", node="router", usage=Usage(80, 20, reasoning_tokens=5), model="mock")
        await bus.emit_token_usage(
            "run-1", node="s1", usage=Usage(40, 12, reasoning_tokens=3), model="mock")
        return bus.usage_totals("run-1")

    totals = asyncio.run(run())
    assert totals["calls"] == 2
    assert totals["tokens"] == 152
    assert totals["prompt"] == 120
    assert totals["completion"] == 32
    assert totals["reasoning"] == 8
    assert totals["embed_calls"] == 0


def test_bus_records_planner_call_when_usage_metadata_missing() -> None:
    async def run() -> dict:
        bus = EventBus()
        await bus.emit_token_usage(
            "run-2", node="planner", usage=Usage(), model="mock", allow_empty=True)
        return bus.usage_totals("run-2")

    totals = asyncio.run(run())
    assert totals["calls"] == 1
    assert totals["tokens"] > 0


def test_bus_accumulates_embedding_usage() -> None:
    from app.memory.embeddings import EmbedCall

    async def run() -> dict:
        bus = EventBus()
        await bus.emit_embedding_calls(
            "run-e",
            node="memory.retrieve",
            calls=[EmbedCall(
                tokens=24, cost_usd=0.000001, ms=18, model="voyage-3.5-lite",
                provider="voyage", input_type="query", cached=False, chars=40,
            )],
        )
        return bus.usage_totals("run-e")

    totals = asyncio.run(run())
    assert totals["embed_calls"] == 1
    assert totals["embed_tokens"] == 24
    assert totals["embed_ms"] == 18
    assert totals["cost_usd"] == 0.000001
    assert totals["tokens"] == 0


def test_answer_delta_does_not_inflate_usage() -> None:
    async def run() -> int:
        bus = EventBus()
        await bus.emit_answer_delta("r", "chunk", done=False)
        await bus.emit(make_event(
            EventType.TOKEN_USAGE, "r", node="n",
            prompt=10, completion=5, cost_usd=0.001))
        return int(bus.usage_totals("r")["tokens"])

    assert asyncio.run(run()) == 15


def test_resolve_auto_uses_live_provider_when_keyed() -> None:
    settings = Settings(llm_provider="auto", openai_api_key="cc-test-token")
    assert resolve_llm_provider(settings) == "openai"


def test_resolve_auto_without_key_raises() -> None:
    from app.core.errors import ConfigError

    settings = Settings(llm_provider="auto", openai_api_key=None)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        resolve_llm_provider(settings)


def test_resolve_explicit_mock_is_honored_even_with_a_key() -> None:
    settings = Settings(llm_provider="mock", openai_api_key="cc-test-token")
    assert resolve_llm_provider(settings) == "mock"


def test_settings_accept_any_key_prefix() -> None:
    settings = Settings(
        openai_api_key='  "cc-live-token-123"  ',
        voyage_api_key="voyage-xyz-not-pa",
    )
    assert settings.openai_api_key == "cc-live-token-123"
    assert settings.voyage_api_key == "voyage-xyz-not-pa"


def test_env_file_is_pinned_to_backend_root() -> None:
    from app.core.settings import backend_root, env_file_path

    root = backend_root()
    assert (root / "app" / "core" / "settings.py").is_file()
    assert env_file_path() == root / ".env"


def test_env_file_llm_keys_win_over_process(monkeypatch, tmp_path) -> None:
    import os

    from app.core.settings import load_app_env

    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\nOPENAI_API_KEY=cc-from-file\n", encoding="utf-8")
    monkeypatch.setattr("app.core.settings.env_file_path", lambda: env_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "cc-from-process")
    load_app_env()
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_API_KEY"] == "cc-from-file"
