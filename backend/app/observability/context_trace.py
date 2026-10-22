"""Trace-visible context snapshots for the workflow inspector.

Emits structured memory / binding / prompt sections so the UI can show what
each node received — not just the latest user message.
"""

from __future__ import annotations

import json
from typing import Any

from app.observability.events import EventType, make_event

MAX_SECTION = 6000


def _clip(text: str, *, limit: int = MAX_SECTION) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit + 24
    return f"{text[: limit - 24]}\n… ({omitted} chars truncated)"


def build_context_sections(
    *,
    conversation: str = "",
    episodic_hints: list[dict[str, Any]] | None = None,
    resolved_entities: dict[str, Any] | None = None,
    prompt_bindings: dict[str, Any] | None = None,
    worker_prompt: str = "",
    system_prompt: str = "",
    upstream: dict[str, Any] | None = None,
    note: str = "",
) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    if note.strip():
        sections.append({
            "key": "note",
            "title": "Routing note",
            "content": note.strip(),
            "format": "text",
        })
    if conversation.strip():
        sections.append({
            "key": "conversation",
            "title": "Session memory",
            "content": _clip(conversation.strip()),
            "format": "text",
        })
    if episodic_hints:
        sections.append({
            "key": "episodic_hints",
            "title": "Episodic memory hints",
            "content": _clip(json.dumps(episodic_hints, indent=2, default=str)),
            "format": "json",
        })
    if resolved_entities:
        filtered = {
            k: v for k, v in resolved_entities.items()
            if v is not None and v != "" and v != [] and v != {}
        }
        if filtered:
            sections.append({
                "key": "entities",
                "title": "Resolved entities",
                "content": json.dumps(filtered, indent=2, default=str),
                "format": "json",
            })
    if prompt_bindings:
        sections.append({
            "key": "bindings",
            "title": "Prompt bindings",
            "content": _clip(json.dumps(prompt_bindings, indent=2, default=str)),
            "format": "json",
        })
    if upstream:
        sections.append({
            "key": "upstream",
            "title": "Upstream step outputs",
            "content": _clip(json.dumps(upstream, indent=2, default=str)),
            "format": "json",
        })
    if system_prompt.strip():
        sections.append({
            "key": "system_prompt",
            "title": "System prompt",
            "content": _clip(system_prompt.strip()),
            "format": "text",
        })
    if worker_prompt.strip():
        sections.append({
            "key": "worker_prompt",
            "title": "Agent decision prompt",
            "content": _clip(worker_prompt.strip()),
            "format": "text",
        })
    return sections


async def emit_context_snapshot(
    emit: Any,
    run_id: str,
    node: str,
    /,
    **fields: Any,
) -> None:
    """Emit a context_snapshot trace event when there is something to show."""
    if emit is None:
        return
    sections = build_context_sections(**fields)
    if not sections:
        return
    await emit(make_event(
        EventType.CONTEXT_SNAPSHOT, run_id, node=node, sections=sections))
