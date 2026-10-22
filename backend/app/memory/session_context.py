"""Session-scoped conversation context for routing, planning, and workers.

Loads persisted chat turns from the database (source of truth) and merges any
persisted or in-memory rolling summary. Per-node char budgets keep router,
planner, and worker prompts within their slices of the GLM context window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts import SESSION_MEMORY_POLICY
from app.core.llm_budgets import (
    conversation_tail,
    planner_conversation_chars,
    router_conversation_chars,
    session_context_max_chars,
    worker_context_json_chars,
    worker_conversation_chars,
)
from app.memory import conversation
from app.models import db


@dataclass(frozen=True)
class SessionTurn:
    role: str
    text: str


@dataclass
class SessionContextView:
    session_id: str
    thread_id: str
    summary: str = ""
    recent_turns: list[SessionTurn] = field(default_factory=list)
    memory_window_tokens: int = 0

    def transcript_text(self, *, max_chars: int | None = None) -> str:
        lines: list[str] = []
        if self.summary:
            lines.append(f"[earlier conversation summary] {self.summary}")
        for turn in self.recent_turns:
            prefix = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{prefix}: {turn.text}")
        body = "\n".join(lines)
        if max_chars is not None and len(body) > max_chars:
            body = body[-max_chars:]
        return body

    def for_router(self) -> str:
        return self.transcript_text(max_chars=router_conversation_chars())

    def for_planner(self) -> str:
        return self.transcript_text(max_chars=planner_conversation_chars())

    def for_worker(self) -> str:
        return self.transcript_text(max_chars=worker_conversation_chars())

    def planner_payload(self) -> dict[str, Any]:
        """Structured memory for planner JSON — summary and recent turns separately."""
        recent = [
            {"role": turn.role, "text": conversation_tail(turn.text, 400)}
            for turn in self.recent_turns[-10:]
        ]
        payload: dict[str, Any] = {"recent_turns": recent}
        if self.summary:
            payload["summary"] = conversation_tail(
                self.summary, planner_conversation_chars())
        return payload

    def worker_payload(self) -> dict[str, Any]:
        """Structured session memory for worker agents.

        Workers need explicit policy that prior assistant numbers are not current facts.
        """
        recent = [
            {
                "role": turn.role,
                "text": conversation_tail(turn.text, 800),
            }
            for turn in self.recent_turns[-8:]
        ]
        payload: dict[str, Any] = {
            "policy": SESSION_MEMORY_POLICY,
            "recent_turns": recent,
        }
        if self.summary:
            payload["summary"] = conversation_tail(
                self.summary, worker_context_json_chars() // 2)
        body = json.dumps(payload, default=str)
        if len(body) > worker_context_json_chars():
            payload["recent_turns"] = recent[-4:]
            body = json.dumps(payload, default=str)
        if len(body) > worker_context_json_chars():
            payload.pop("summary", None)
        return payload


def _message_to_turn(msg: Any) -> SessionTurn | None:
    text = str((msg.content or {}).get("text") or "").strip()
    if not text:
        return None
    role = "user" if msg.role == "user" else "assistant"
    return SessionTurn(role=role, text=text)


async def load_session_context(session_id: str, thread_id: str) -> SessionContextView:
    """Hydrate structured session memory from DB + in-memory L2 window."""
    record = await db.get_session_record(session_id)
    persisted_summary = ""
    persisted_tokens = 0
    if record is not None:
        persisted_summary = (record.conversation_summary or "").strip()
        persisted_tokens = int(record.memory_window_tokens or 0)

    win = conversation.window_for(thread_id)
    conversation.hydrate_window(win, persisted_summary, persisted_tokens)

    summary = win.summary or persisted_summary
    messages = await db.list_messages(session_id, limit=50)
    turns: list[SessionTurn] = []
    for msg in messages:
        turn = _message_to_turn(msg)
        if turn is not None:
            turns.append(turn)

    return SessionContextView(
        session_id=session_id,
        thread_id=thread_id,
        summary=summary,
        recent_turns=turns,
        memory_window_tokens=max(win.token_count, persisted_tokens),
    )


def render_transcript(view: SessionContextView, *, max_chars: int | None = None) -> str:
    """Render a flat transcript string from a structured view."""
    return view.transcript_text(max_chars=max_chars)


async def build_session_context(
    session_id: str,
    thread_id: str,
    *,
    max_chars: int | None = None,
) -> str:
    """Recent transcript for this session: DB messages + optional rolling summary."""
    view = await load_session_context(session_id, thread_id)
    limit = max_chars if max_chars is not None else session_context_max_chars()
    return view.transcript_text(max_chars=limit)
