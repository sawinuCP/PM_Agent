"""HTTP-boundary regressions for auth and live SSE wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from app.core.settings import get_settings
from app.main import create_app
from app.models import db
from app.observability.bus import EventBus


async def test_mutating_route_requires_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-api-token")
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(
            "/api/chat", json={"session_id": "missing", "message": "hello"})
    assert denied.status_code == 401


async def test_sse_subscribes_to_platform_event_bus(monkeypatch) -> None:
    from app.api import runs

    monkeypatch.delenv("API_TOKEN", raising=False)
    get_settings.cache_clear()
    await db.init_schema()
    session = await db.create_session_record("sse", "demo")
    await db.create_run_record(
        session.id, session.thread_id, "stream", run_id="run-sse")
    event_bus = EventBus()
    monkeypatch.setattr(
        runs, "get_platform", lambda: SimpleNamespace(bus=event_bus))

    async def emit_terminal() -> None:
        await asyncio.sleep(0.01)
        await event_bus.emit_run_finished(
            "run-sse", "streamed result", {"tokens": 0, "cost_usd": 0})

    transport = httpx.ASGITransport(app=create_app())
    emit_task = asyncio.create_task(emit_terminal())
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream("GET", "/api/runs/run-sse/events") as response,
    ):
        body = "".join([chunk async for chunk in response.aiter_text()])
    await emit_task
    assert response.status_code == 200
    assert '"type": "run_finished"' in body
    assert "streamed result" in body


async def test_sse_closes_when_embeddings_follow_run_finished(monkeypatch) -> None:
    from app.api import runs
    from app.observability.events import EventType, make_event

    monkeypatch.delenv("API_TOKEN", raising=False)
    get_settings.cache_clear()
    await db.init_schema()
    session = await db.create_session_record("sse-embed", "demo")
    await db.create_run_record(
        session.id, session.thread_id, "done already", run_id="run-sse-embed")
    event_bus = EventBus()
    monkeypatch.setattr(
        runs, "get_platform", lambda: SimpleNamespace(bus=event_bus))
    await event_bus.emit_run_finished(
        "run-sse-embed", "ok", {"tokens": 0, "cost_usd": 0}, stream=False)
    await event_bus.emit(make_event(
        EventType.EMBEDDING_USAGE, "run-sse-embed",
        node="memory.write", model="voyage", provider="voyage",
        tokens=12, cost_usd=0.0, ms=8, input_type="document", cached=False))

    transport = httpx.ASGITransport(app=create_app())
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream("GET", "/api/runs/run-sse-embed/events") as response,
    ):
        body = "".join([chunk async for chunk in response.aiter_text()])
    assert response.status_code == 200
    assert '"type": "run_finished"' in body
    assert '"type": "embedding_usage"' in body
