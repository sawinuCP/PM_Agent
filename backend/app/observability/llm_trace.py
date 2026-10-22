"""Compact LLM request/response previews for trace events."""

from __future__ import annotations

import json
from typing import Any

_MAX_PREVIEW = 6000


def truncate_trace_text(text: str, *, max_chars: int = _MAX_PREVIEW) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    half = max(400, max_chars // 2)
    omitted = len(cleaned) - (half * 2)
    return (
        f"{cleaned[:half]}\n\n… [{omitted:,} chars truncated] …\n\n{cleaned[-half:]}"
    )


def preview_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "?")
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"[{role}]\n{content}")
        elif content is not None:
            parts.append(f"[{role}]\n{json.dumps(content, default=str)[:2000]}")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            parts.append(
                f"[{role} tool_calls]\n"
                f"{json.dumps(tool_calls, default=str)[:2500]}"
            )
    return truncate_trace_text("\n\n".join(parts))


def preview_simple(system: str, user: str) -> str:
    return truncate_trace_text(f"[system]\n{system}\n\n[user]\n{user}")


def preview_completion(
    content: str | None,
    *,
    tool_calls: list[Any] | None = None,
) -> str:
    parts: list[str] = []
    if content and content.strip():
        parts.append(content.strip())
    if tool_calls:
        rows = []
        for tc in tool_calls:
            if hasattr(tc, "name"):
                rows.append({"name": tc.name, "args": getattr(tc, "args", {})})
            elif isinstance(tc, dict):
                rows.append(tc)
        if rows:
            parts.append("tool_calls:\n" + json.dumps(rows, default=str, indent=2))
    return truncate_trace_text("\n\n".join(parts) if parts else "(empty response)")
