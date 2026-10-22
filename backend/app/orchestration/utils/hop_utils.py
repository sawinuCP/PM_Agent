"""Shared helpers for worker hop lists."""

from __future__ import annotations

from typing import Any


def ok_hops(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Successful tool_result hops only."""
    return [
        hop for hop in hops
        if hop.get("type") == "tool_result" and hop.get("status") == "ok"
    ]


def parse_id_list(raw: Any) -> list[str]:
    """Normalize comma-separated or list tool args into string ids."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]
