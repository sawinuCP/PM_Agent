"""OpenAI-compatible tool specs built from MCP catalogs."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9_]+")


def normalize_parameters(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce MCP input_schema into an OpenAI function parameters object."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    params = dict(schema)
    params.setdefault("type", "object")
    if params["type"] != "object":
        params = {"type": "object", "properties": {"value": params}}
    params.setdefault("properties", {})
    if not isinstance(params["properties"], dict):
        params["properties"] = {}
    required = params.get("required")
    if required is not None and not isinstance(required, list):
        params.pop("required", None)
    return params


def catalog_entry_to_openai_tool(entry: dict[str, Any]) -> dict[str, Any]:
    """One MCP catalog row → OpenAI Chat Completions tool definition."""
    name = str(entry.get("name") or "")
    description = str(entry.get("description") or name)[:512]
    schema = entry.get("input_schema")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": normalize_parameters(schema if isinstance(schema, dict) else None),
        },
    }


def build_openai_tools(
    manager: Any,
    tool_names: list[str],
) -> list[dict[str, Any]]:
    """Build API tool definitions for the bound allowlist."""
    by_name: dict[str, dict[str, Any]] = {}
    for catalog in manager.tool_catalog().values():
        for item in catalog:
            name = item.get("name")
            if isinstance(name, str):
                by_name[name] = item
    tools: list[dict[str, Any]] = []
    for name in tool_names:
        entry = by_name.get(name)
        if entry:
            tools.append(catalog_entry_to_openai_tool(entry))
    return tools


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _score_tool(name: str, description: str, query_tokens: set[str]) -> float:
    hay = _tokens(f"{name} {description}".replace("_", " "))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & hay)
    # Boost exact name fragments (e.g. "project" → jira_get_projects).
    for token in query_tokens:
        if len(token) >= 4 and token in name.lower():
            overlap += 2
    return float(overlap)


def shortlist_tool_names(
    tool_names: list[str],
    catalog_by_name: dict[str, dict[str, Any]],
    *,
    goal: str,
    task: str,
    max_tools: int,
    pinned: set[str] | None = None,
) -> list[str]:
    """Return top-N tool names by lightweight relevance to goal + task."""
    if len(tool_names) <= max_tools:
        return list(tool_names)
    query = _tokens(f"{goal} {task}")
    pinned = pinned or set()
    ranked: list[tuple[float, str]] = []
    for name in tool_names:
        entry = catalog_by_name.get(name, {})
        desc = str(entry.get("description") or name)
        score = _score_tool(name, desc, query)
        if name in pinned:
            score += 100.0
        ranked.append((score, name))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    chosen = [name for _, name in ranked[:max_tools]]
    # Preserve stable order for pinned tools that fell outside top-N.
    for name in tool_names:
        if name in pinned and name not in chosen:
            chosen[-1] = name
    return chosen
