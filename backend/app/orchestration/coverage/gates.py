"""Task coverage gate — block worker finish when evidence scope is incomplete.

Generic heuristics from planner task language + prior_hops shapes.
"""

from __future__ import annotations

from typing import Any

from app.orchestration.coverage.entity_coverage import entity_finish_blocked_reason
from app.orchestration.tooling.tool_chains import chain_gaps
from app.orchestration.tooling.tool_harvest import (
    _SCOPE_ALL_RE,
    _task_needs_broad_sprint_fetch,
    discovered_entities,
)
from app.orchestration.utils.hop_utils import ok_hops, parse_id_list

_SPRINT_FETCH_TOOLS = frozenset({
    "jira_get_sprints",
    "jira_get_sprint",
    "jira_get_sprint_issues",
    "jira_get_sprint_report",
})

_LIST_DISCOVERY_TOOLS = frozenset({
    "jira_get_projects",
    "jira_get_project",
})


def _entities_touched(hops: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Project/board keys present in successful hop args or result payloads."""
    projects: set[str] = set()
    boards: set[str] = set()

    for hop in ok_hops(hops):
        for key in discovered_entities([hop]).get("projects") or []:
            projects.add(key)
        for key in discovered_entities([hop]).get("boards") or []:
            boards.add(key)

        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        for key in parse_id_list(args.get("projects")):
            projects.add(key.upper())
        project = args.get("project")
        if isinstance(project, str) and project.strip():
            projects.add(project.strip().upper())
        board = args.get("board_id")
        if isinstance(board, str) and board.strip():
            boards.add(board.strip())
        for bid in parse_id_list(args.get("board_ids")):
            boards.add(bid)

        data = hop.get("data")
        if isinstance(data, dict):
            for sprint in data.get("sprints") or []:
                if isinstance(sprint, dict) and sprint.get("board_id"):
                    boards.add(str(sprint["board_id"]).strip())

    return {"projects": projects, "boards": boards}


def _discovered_catalog(hops: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Entities listed by an explicit discovery/list tool in this step."""
    projects: set[str] = set()
    boards: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _LIST_DISCOVERY_TOOLS:
            continue
        found = discovered_entities([hop])
        projects.update(found.get("projects") or [])
        boards.update(found.get("boards") or [])
    if not projects and not boards:
        found = discovered_entities(hops)
        projects.update(found.get("projects") or [])
        boards.update(found.get("boards") or [])
    return {"projects": projects, "boards": boards}


def _sprint_fetch_coverage(hops: list[dict[str, Any]]) -> dict[str, set[str]]:
    projects: set[str] = set()
    boards: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _SPRINT_FETCH_TOOLS:
            continue
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        for key in parse_id_list(args.get("projects")):
            projects.add(key.upper())
        project = args.get("project")
        if isinstance(project, str) and project.strip():
            projects.add(project.strip().upper())
        board = args.get("board_id")
        if isinstance(board, str) and board.strip():
            boards.add(board.strip())
        for bid in parse_id_list(args.get("board_ids")):
            boards.add(bid)
    return {"projects": projects, "boards": boards}


def task_requires_multi_entity_scope(task: str) -> bool:
    return bool(_SCOPE_ALL_RE.search(task or ""))


def coverage_gaps(task: str, hops: list[dict[str, Any]]) -> list[str]:
    """Return human-readable gaps when finish would be premature; empty if OK."""
    if not task_requires_multi_entity_scope(task):
        return []
    if not ok_hops(hops):
        return ["No successful tool results yet for a multi-entity task."]

    catalog = _discovered_catalog(hops)
    disc_projects = catalog["projects"]
    disc_boards = catalog["boards"]

    if len(disc_projects) < 2 and len(disc_boards) < 2:
        return []

    if _task_needs_broad_sprint_fetch(task):
        fetch_cov = _sprint_fetch_coverage(hops)
        target = max(len(disc_projects), len(disc_boards), 2)
        proj_n = len(fetch_cov["projects"])
        board_n = len(fetch_cov["boards"])
        if disc_projects and disc_projects <= fetch_cov["projects"]:
            return []
        if disc_boards and disc_boards <= fetch_cov["boards"]:
            return []
        if board_n >= target or proj_n >= target:
            return []
        missing = disc_projects - fetch_cov["projects"]
        label = sorted(missing) if missing else sorted(disc_projects)
        return [
            "Sprint history incomplete for broad task scope — "
            f"covered {max(proj_n, board_n)}/{target} entities "
            f"(missing projects: {label}). "
            "Use batch projects= or parallel per-board/project fetches before finish.",
        ]

    touched = _entities_touched(hops)
    if disc_projects:
        missing = disc_projects - touched["projects"]
        if missing:
            return [
                "Task scope requires all discovered projects but evidence lacks: "
                f"{sorted(missing)}.",
            ]
    return []


def finish_blocked_reason(
    task: str,
    hops: list[dict[str, Any]],
    *,
    goal: str = "",
    bound_tools: set[str] | None = None,
    upstream_hops: list[dict[str, Any]] | None = None,
) -> str | None:
    gaps = coverage_gaps(task, hops)
    entity_block = entity_finish_blocked_reason(
        task=task, goal=goal, hops=hops, upstream_hops=upstream_hops,
    )
    if entity_block:
        gaps.append(entity_block)
    if bound_tools:
        gaps = gaps + chain_gaps(task, hops, bound_tools=bound_tools)
    if not gaps:
        return None
    return "; ".join(gaps)
