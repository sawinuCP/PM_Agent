"""Worker tool-error recovery — semantic retry after MCP validation failures.

Transport timeout/retry lives in mcp_integration/resilience.py (Manager layer).
This module handles application-level tool errors returned in payloads, e.g.
`{"error": "version is required"}` when args are incomplete.
"""

from __future__ import annotations

import json
import re
from typing import Any

_REQUIRED_ARG_RE = re.compile(
    r"(?P<field>[a-z_]+)\s+is\s+required|missing\s+(?:required\s+)?(?:arg|parameter)\s+['\"]?(?P<field2>[a-z_]+)",
    re.IGNORECASE,
)


def _hop_signature(tool: str, args: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)


def _error_text(hop: dict[str, Any]) -> str:
    data = hop.get("data")
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    digest = hop.get("digest")
    if isinstance(digest, str) and digest.strip():
        return digest
    return "tool call failed"


def unresolved_tool_errors(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failed tool hops with no later successful call to the same tool."""
    ok_tools = {
        str(hop.get("tool") or "")
        for hop in hops
        if hop.get("type") == "tool_result" and hop.get("status") == "ok"
    }
    pending: dict[str, dict[str, Any]] = {}
    for hop in hops:
        if hop.get("type") != "tool_result":
            continue
        tool = str(hop.get("tool") or "")
        if not tool:
            continue
        if hop.get("status") == "ok":
            pending.pop(tool, None)
            continue
        pending[tool] = hop
    return [
        hop for tool, hop in pending.items()
        if tool not in ok_tools
    ]


def error_attempt_count(
    hops: list[dict[str, Any]], tool_name: str, args: dict[str, Any],
) -> int:
    sig = _hop_signature(tool_name, args)
    count = 0
    for hop in hops:
        if hop.get("type") != "tool_result" or hop.get("status") == "ok":
            continue
        hop_tool = str(hop.get("tool") or "")
        hop_args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        if _hop_signature(hop_tool, hop_args) == sig:
            count += 1
    return count


def suggest_recovery_calls(hop: dict[str, Any]) -> list[dict[str, Any]]:
    """Generic recovery hints from error text + failed tool (no Jira routing tables)."""
    tool = str(hop.get("tool") or "")
    args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
    err = _error_text(hop).lower()
    suggestions: list[dict[str, Any]] = []

    match = _REQUIRED_ARG_RE.search(err)
    missing_field = (match.group("field") or match.group("field2") if match else "") or ""

    if tool == "jira_get_release_status" and (
        "version" in err or missing_field == "version"
    ):
        project = str(args.get("project") or "").strip()
        if project:
            suggestions.append({
                "tool": "jira_get_releases",
                "args": {"project": project},
                "reason": (
                    "Discover release/version names before jira_get_release_status."
                ),
            })
        suggestions.append({
            "tool": "jira_get_release_status",
            "args": {
                "project": project or args.get("project", ""),
                "version": "<pick from jira_get_releases>",
            },
            "reason": "Retry release status with an explicit version name.",
        })
    elif missing_field:
        suggestions.append({
            "tool": tool,
            "args": {**args, missing_field: f"<required {missing_field}>"},
            "reason": f"Retry with required field '{missing_field}' filled in.",
        })
    return suggestions


def finish_blocked_on_tool_errors(hops: list[dict[str, Any]]) -> str | None:
    """Block finish while fixable tool errors remain unresolved."""
    errors = unresolved_tool_errors(hops)
    if not errors:
        return None
    parts: list[str] = []
    for hop in errors[:4]:
        tool = hop.get("tool") or "tool"
        parts.append(f"{tool}: {_error_text(hop)}")
    return (
        "Unresolved tool errors — "
        + "; ".join(parts)
        + ". Read the error, fix args using tool schema, or call a discovery tool "
        "before retrying. Do not finish with silent gaps."
    )
