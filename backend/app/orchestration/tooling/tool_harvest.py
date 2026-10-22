"""Generic harvest hints from prior tool hops.

After list/discovery tools return structured envelopes (projects[], sprints[]),
derive discovered entity IDs and suggest batch follow-up calls.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestration.tooling.tool_retry import (
    suggest_recovery_calls,
    unresolved_tool_errors,
)

_PROJECT_KEYS = ("projects", "items", "by_project")
_SPRINT_KEYS = ("sprints", "by_sprint")
_BOARD_KEYS = ("boards", "board_ids")

_SCOPE_ALL_RE = re.compile(
    r"\b(all|every|each|across|per\s+(project|board)|multi(?:ple)?|"
    r"accessible\s+projects?|all\s+projects?|every\s+board)\b",
    re.IGNORECASE,
)
_SPRINT_HISTORY_RE = re.compile(
    r"\b(sprint|velocity|forecast|closed|historical|completed\s+points?|"
    r"story\s+points?|burndown|trend)\b",
    re.IGNORECASE,
)


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _project_keys_from_row(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("key", "project", "id"):
        val = row.get(field)
        if isinstance(val, str) and val.strip():
            keys.append(val.strip().upper())
    return keys


def discovered_entities(hops: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Extract project / sprint / board ids seen in successful tool results."""
    projects: list[str] = []
    sprints: list[str] = []
    boards: list[str] = []

    for hop in hops:
        if hop.get("type") != "tool_result" or hop.get("status") != "ok":
            continue
        data = hop.get("data")
        if not isinstance(data, dict):
            continue

        for key in _PROJECT_KEYS:
            bucket = data.get(key)
            if isinstance(bucket, list):
                for row in bucket:
                    if isinstance(row, dict):
                        projects.extend(_project_keys_from_row(row))
                    elif isinstance(row, str):
                        projects.append(row.strip().upper())
            elif isinstance(bucket, dict):
                for sub_key, row in bucket.items():
                    if isinstance(row, dict):
                        projects.extend(_project_keys_from_row(row))
                    elif isinstance(sub_key, str):
                        projects.append(sub_key.strip().upper())

        for key in _SPRINT_KEYS:
            bucket = data.get(key)
            if isinstance(bucket, list):
                for row in bucket:
                    if isinstance(row, dict):
                        sid = row.get("id") or row.get("sprint_id")
                        if isinstance(sid, str):
                            sprints.append(sid.strip())
                        nested = row.get("sprint")
                        if isinstance(nested, dict) and nested.get("id"):
                            sprints.append(str(nested["id"]).strip())
            elif isinstance(bucket, dict):
                for sid in bucket:
                    sprints.append(str(sid).strip())

        for key in _BOARD_KEYS:
            bucket = data.get(key)
            if isinstance(bucket, list):
                for row in bucket:
                    if isinstance(row, dict) and row.get("id"):
                        boards.append(str(row["id"]).strip())
                    elif isinstance(row, str):
                        boards.append(row.strip())
            elif isinstance(bucket, str):
                boards.extend(part.strip() for part in bucket.split(",") if part.strip())

        single_project = data.get("project")
        if isinstance(single_project, str) and single_project.strip():
            projects.append(single_project.strip().upper())

    return {
        "projects": _unique_strings(projects),
        "sprints": _unique_strings(sprints),
        "boards": _unique_strings(boards),
    }


def _task_needs_broad_sprint_fetch(task: str) -> bool:
    text = (task or "").strip()
    if not text:
        return False
    return bool(_SCOPE_ALL_RE.search(text) and _SPRINT_HISTORY_RE.search(text))


def suggest_batch_calls(
    discovered: dict[str, list[str]], *, task: str,
) -> list[dict[str, Any]]:
    """Non-binding suggestions for the worker LLM (batch args when scope is wide)."""
    suggestions: list[dict[str, Any]] = []
    projects = discovered.get("projects") or []
    if len(projects) >= 2 and _task_needs_broad_sprint_fetch(task):
        joined = ",".join(projects)
        suggestions.append({
            "tool": "jira_get_sprints",
            "args": {
                "projects": joined,
                "state": "closed",
                "last_n": 10,
                "include_metrics": True,
            },
            "reason": (
                f"Task scope is multi-project; one batch call covers {len(projects)} "
                "projects instead of serial per-project fetches."
            ),
        })
        suggestions.append({
            "tool": "jira_get_project",
            "args": {"projects": joined},
            "reason": "Optional project metadata batch for all discovered keys.",
        })
    elif len(projects) == 1 and _task_needs_broad_sprint_fetch(task):
        suggestions.append({
            "tool": "jira_get_sprints",
            "args": {
                "project": projects[0],
                "state": "closed",
                "last_n": 10,
                "include_metrics": True,
            },
            "reason": "Fetch enough closed sprints for trend/velocity work.",
        })
    return suggestions


def harvest_context(hops: list[dict[str, Any]], *, task: str) -> dict[str, Any] | None:
    """Build harvest block for worker payload, or None when empty."""
    discovered = discovered_entities(hops)
    if not any(discovered.values()):
        return None
    suggestions = suggest_batch_calls(discovered, task=task)
    block: dict[str, Any] = {"discovered": discovered}
    if suggestions:
        block["suggested_next_calls"] = suggestions
    return block


def enrich_worker_body(
    body: dict[str, Any],
    *,
    hops: list[dict[str, Any]],
    task: str,
    bound_tools: set[str] | None = None,
) -> None:
    """Mutate worker JSON body with factual discovery context from prior hops."""
    _ = bound_tools
    block = harvest_context(hops, task=task)
    if block is None:
        return
    next_calls = block.get("suggested_next_calls")
    if isinstance(next_calls, list):
        block["suggested_next_calls"] = next_calls[:4]
    body["harvest"] = block

    errors = unresolved_tool_errors(hops)
    if errors:
        recovery: list[dict[str, Any]] = []
        for hop in errors[:4]:
            recovery.extend(suggest_recovery_calls(hop))
        if recovery:
            body.setdefault("harvest", {})["suggested_recovery"] = recovery[:4]
