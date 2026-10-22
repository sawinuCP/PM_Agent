"""Tool hop helpers — compact previews for worker context (not full payloads)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.mcp_integration.digest import structured_preview

logger = logging.getLogger(__name__)

_WORKER_HOP_WINDOW = 8
_MESSAGE_MAX_CHARS = 8_000


def hop_data_from_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Use structured preview for worker context; avoid dumping full MCP payloads."""
    preview = outcome.get("preview")
    if isinstance(preview, dict) and preview:
        return preview
    digest = outcome.get("digest")
    if isinstance(digest, str) and digest.strip():
        return {"digest": digest[:2000]}
    return {}


def build_tool_hop(
    *,
    tool_name: str,
    args: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Construct a tool_result hop with preview data for the ReAct loop."""
    return {
        "type": "tool_result",
        "tool": tool_name,
        "status": outcome.get("status") or "error",
        "result_id": outcome.get("result_id"),
        "args": args,
        "digest": outcome.get("digest"),
        "data": hop_data_from_outcome(outcome),
    }


async def hydrate_hop_data(hop: dict[str, Any]) -> dict[str, Any]:
    """No-op — workers use inline previews only."""
    return hop


async def hydrate_hops(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """No-op — full payload hydration removed."""
    return list(hops)


def prior_hops_for_worker(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recent hops for the current step (sliding window)."""
    return list(hops[-_WORKER_HOP_WINDOW:])


def hop_message_content(hop: dict[str, Any], *, max_chars: int = _MESSAGE_MAX_CHARS) -> str:
    """Serialize hop preview for native tool message channels."""
    if hop.get("type") == "tool_result":
        payload: dict[str, Any] = {
            "tool": hop.get("tool"),
            "status": hop.get("status"),
            "args": hop.get("args"),
            "data": hop.get("data"),
        }
        digest = hop.get("digest")
        if digest and not payload.get("data"):
            payload["digest"] = digest
    else:
        payload = hop  # type: ignore[assignment]
    text = json.dumps(payload, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + '\n..."[truncated]"'
