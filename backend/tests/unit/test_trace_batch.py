"""Tests for batched trace persistence in EventBus."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models import db
from app.observability.bus import EventBus
from app.observability.events import EventType, make_event


@pytest.mark.asyncio
async def test_trace_buffer_flushes_on_batch_size() -> None:
    bus = EventBus(trace_batch_size=3)
    batches: list[list] = []

    async def capture(rows: list) -> None:
        batches.append(list(rows))

    with patch.object(db, "save_traces_batch", new=AsyncMock(side_effect=capture)):
        for index in range(5):
            await bus.emit(make_event(
                EventType.NODE_STARTED, "run-batch",
                node=f"s{index}",
            ))

        assert len(batches) == 1
        assert len(batches[0]) == 3

        await bus.flush_traces("run-batch")
        assert len(batches) == 2
        assert len(batches[1]) == 2


@pytest.mark.asyncio
async def test_terminal_event_flushes_remaining_buffer() -> None:
    bus = EventBus(trace_batch_size=10)
    save_mock = AsyncMock()
    with patch.object(db, "save_traces_batch", save_mock):
        await bus.emit(make_event(EventType.RUN_STARTED, "run-term", goal="hi"))
        await bus.emit(make_event(EventType.NODE_STARTED, "run-term", node="s1"))
        assert save_mock.await_count == 0

        await bus.emit(make_event(
            EventType.RUN_FINISHED, "run-term", result="done", totals={},
        ))

    assert save_mock.await_count == 1
    batch = save_mock.await_args_list[0].args[0]
    assert len(batch) == 3
    assert batch[-1][2] == EventType.RUN_FINISHED.value


@pytest.mark.asyncio
async def test_post_terminal_embedding_flushes_immediately() -> None:
    bus = EventBus(trace_batch_size=10)
    save_mock = AsyncMock()
    with patch.object(db, "save_traces_batch", save_mock):
        await bus.emit(make_event(
            EventType.RUN_FINISHED, "run-embed", result="ok", totals={},
        ))
        assert save_mock.await_count == 1
        await bus.emit(make_event(
            EventType.EMBEDDING_USAGE, "run-embed",
            node="memory.write", model="m", provider="p",
            tokens=1, cost_usd=0.0, ms=1, input_type="document", cached=False,
        ))

    assert save_mock.await_count == 2
    assert save_mock.await_args_list[1].args[0][0][2] == EventType.EMBEDDING_USAGE.value


@pytest.mark.asyncio
async def test_answer_delta_is_not_persisted() -> None:
    bus = EventBus(trace_batch_size=4)
    save_mock = AsyncMock()
    with patch.object(db, "save_traces_batch", save_mock):
        await bus.emit(make_event(EventType.ANSWER_DELTA, "run-delta", text="x"))
        await bus.flush_traces("run-delta")

    save_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_all_traces_drains_every_run() -> None:
    bus = EventBus(trace_batch_size=50)
    save_mock = AsyncMock()
    with patch.object(db, "save_traces_batch", save_mock):
        await bus.emit(make_event(EventType.RUN_STARTED, "run-a", goal="a"))
        await bus.emit(make_event(EventType.RUN_STARTED, "run-b", goal="b"))
        await bus.flush_all_traces()

    assert save_mock.await_count == 2
    flushed_run_ids = {call.args[0][0][0] for call in save_mock.await_args_list}
    assert flushed_run_ids == {"run-a", "run-b"}


@pytest.mark.asyncio
async def test_save_traces_batch_persists_rows(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'traces.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings
    from app.models.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    await db.init_schema()

    run_id = "run-save-batch"
    rows = [
        (run_id, 0, "run_started", None, {"type": "run_started", "seq": 0}),
        (run_id, 1, "node_started", "s1", {"type": "node_started", "seq": 1, "node": "s1"}),
    ]
    await db.save_traces_batch(rows)
    stored = await db.list_traces(run_id)
    assert len(stored) == 2
    assert stored[0].seq == 0
    assert stored[1].event_type == "node_started"


def test_integration_style_emit_then_list_traces(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'traces-int.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings
    from app.models.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()

    async def run() -> int:
        await db.init_schema()
        bus = EventBus(trace_batch_size=4)
        run_id = "run-int"
        await bus.emit(make_event(EventType.RUN_STARTED, run_id, goal="test"))
        await bus.emit(make_event(EventType.ROUTER_DECISION, run_id,
                                 intent="simple", path="fast"))
        await bus.emit(make_event(
            EventType.RUN_FINISHED, run_id, result="ok", totals={"tokens": 1},
        ))
        return len(await db.list_traces(run_id))

    assert asyncio.run(run()) == 3
