"""Session conversation memory: transcript loading."""

from __future__ import annotations

import pytest

from app.core.llm_budgets import (
    planner_conversation_chars,
    router_conversation_chars,
    worker_conversation_chars,
)
from app.memory.conversation import ConversationWindow, hydrate_window, persist_window
from app.memory.session_context import (
    SessionContextView,
    SessionTurn,
    build_session_context,
    load_session_context,
)


def test_session_context_view_slices() -> None:
    long_text = "x" * 5000
    view = SessionContextView(
        session_id="s1",
        thread_id="t1",
        summary="Earlier sprint review covered blockers.",
        recent_turns=[
            SessionTurn(role="user", text=long_text),
            SessionTurn(role="assistant", text="Acknowledged."),
        ],
    )
    router = view.for_router()
    planner = view.for_planner()
    worker = view.for_worker()
    assert len(router) <= router_conversation_chars()
    assert len(planner) <= planner_conversation_chars()
    assert len(worker) <= worker_conversation_chars()
    assert "Earlier sprint review" in planner
    assert view.planner_payload()["summary"]
    worker_payload = view.worker_payload()
    assert worker_payload["policy"]
    assert worker_payload["recent_turns"]


async def test_build_session_context_truncates(monkeypatch) -> None:
    async def fake_list_messages(_session_id: str, *, limit: int = 40):  # noqa: ARG001
        return [
            type("Msg", (), {
                "role": "user",
                "content": {"text": "hello"},
            })(),
        ]

    async def fake_get_session_record(_session_id: str):  # noqa: ARG001
        return type("Rec", (), {"conversation_summary": None, "memory_window_tokens": 0})()

    monkeypatch.setattr("app.memory.session_context.db.list_messages", fake_list_messages)
    monkeypatch.setattr("app.memory.session_context.db.get_session_record", fake_get_session_record)
    body = await build_session_context("s1", "t1", max_chars=100)
    assert "hello" in body


@pytest.mark.asyncio
async def test_load_session_context_hydrates_summary(monkeypatch) -> None:
    async def fake_list_messages(_session_id: str, *, limit: int = 40):  # noqa: ARG001
        return [
            type("Msg", (), {"role": "user", "content": {"text": "Hi"}})(),
        ]

    async def fake_get_session_record(_session_id: str):  # noqa: ARG001
        return type("Rec", (), {
            "conversation_summary": "User asked about sprint 3.",
            "memory_window_tokens": 120,
        })()

    monkeypatch.setattr("app.memory.session_context.db.list_messages", fake_list_messages)
    monkeypatch.setattr("app.memory.session_context.db.get_session_record", fake_get_session_record)

    view = await load_session_context("s1", "thread-hydrate")
    assert "sprint 3" in view.summary
    assert view.recent_turns[-1].text == "Hi"
    assert view.memory_window_tokens >= 120


def test_hydrate_window_from_persisted_state() -> None:
    win = ConversationWindow(thread_id="t1", budget=1000)
    hydrate_window(win, "Persisted summary.", 500)
    assert win.summary == "Persisted summary."
    assert win.token_count == 500


@pytest.mark.asyncio
async def test_persist_window_writes_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_update(session_id: str, **fields: object) -> None:
        captured["session_id"] = session_id
        captured.update(fields)

    monkeypatch.setattr("app.models.db.update_session_record", fake_update)
    win = ConversationWindow(thread_id="t1", budget=1000, summary="Rolling summary.")
    win.token_count = 42
    await persist_window("sess-1", win)
    assert captured["session_id"] == "sess-1"
    assert captured["conversation_summary"] == "Rolling summary."
    assert captured["memory_window_tokens"] == 42
