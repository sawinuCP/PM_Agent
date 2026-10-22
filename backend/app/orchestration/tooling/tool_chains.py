"""Tool prerequisite chains — discovery-before-detail without keyword routing.

Driven by MCP tool semantics (common Jira patterns). Used for harvest hints,
shortlist pinning, coverage gates, and arg enrichment — not hardcoded plan repair.
"""

from __future__ import annotations

import re
from typing import Any

# Consumer tool → discovery tool(s) that should run first in the same step.
TOOL_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "jira_get_sprint_issues": ("jira_get_sprints", "jira_get_sprint"),
    "jira_get_sprint_report": ("jira_get_sprints", "jira_get_sprint"),
    "jira_get_release_status": ("jira_get_releases",),
    "jira_get_project_health": ("jira_get_projects", "jira_get_project"),
    "jira_get_team_workload": ("jira_get_team_members", "jira_get_users"),
    "jira_get_blocked_issues": ("jira_get_projects", "jira_get_project"),
}

_SPRINT_ISSUE_TASK = re.compile(
    r"\b(sprint\s+issues?|open\s+tickets?|tickets?\s+in\s+(this|the|current)\s+sprint|"
    r"backlog\s+items?)\b",
    re.IGNORECASE,
)
_SPRINT_METRICS_TASK = re.compile(
    r"\b(sprint|velocity|burndown|forecast|closed\s+sprints?|sprint\s+history|"
    r"sprint\s+report|remaining\s+work)\b",
    re.IGNORECASE,
)
_RELEASE_TASK = re.compile(r"\b(release|version|milestone)\b", re.IGNORECASE)
_HEALTH_TASK = re.compile(r"\b(health|on\s+track|risk|blocked|overdue|stuck)\b", re.IGNORECASE)
_BUG_COUNT_TASK = re.compile(
    r"\b(bug\s+counts?|bugs?\s+(per|across|in)\s+sprint|bug-type|bug\s+trend|"
    r"defect\s+counts?|count\s+bugs?)\b",
    re.IGNORECASE,
)


def _ok_tool_names(hops: list[dict[str, Any]]) -> set[str]:
    return {
        str(hop.get("tool") or "")
        for hop in hops
        if hop.get("type") == "tool_result" and hop.get("status") == "ok"
    }


def likely_tools_for_task(task: str) -> set[str]:
    """Heuristic targets implied by task wording (for pinning / coverage)."""
    text = task or ""
    tools: set[str] = set()
    if _SPRINT_ISSUE_TASK.search(text) or _BUG_COUNT_TASK.search(text):
        tools.update({"jira_get_sprints", "jira_get_sprint_issues"})
    if _BUG_COUNT_TASK.search(text):
        tools.update({"jira_get_sprint_report", "jira_get_sprints"})
    if _SPRINT_METRICS_TASK.search(text):
        tools.update({"jira_get_sprints", "jira_get_sprint_report"})
    if _RELEASE_TASK.search(text):
        tools.update({"jira_get_releases", "jira_get_release_status"})
    if _HEALTH_TASK.search(text):
        tools.update({"jira_get_projects", "jira_get_project_health"})
    if re.search(r"\bworkload|overloaded|capacity\b", text, re.I):
        tools.add("jira_get_team_workload")
    return tools


def chain_gaps(task: str, hops: list[dict[str, Any]], *, bound_tools: set[str]) -> list[str]:
    """Gaps when a tool the agent already called lacks its discovery prerequisite."""
    _ = task
    gaps: list[str] = []
    ok = _ok_tool_names(hops)
    for tool in sorted(ok):
        if tool not in bound_tools:
            continue
        for prereq in TOOL_PREREQUISITES.get(tool, ()):
            if prereq in bound_tools and prereq not in ok:
                gaps.append(
                    f"Called {tool} but prerequisite {prereq} has no successful result — "
                    f"run {prereq} first to obtain ids/versions for args.",
                )
                break
    return gaps


def discovery_tools_for_completed(completed: set[str]) -> set[str]:
    """Pin missing prerequisite tools only for tools already attempted this step."""
    pinned: set[str] = set()
    for tool in completed:
        for prereq in TOOL_PREREQUISITES.get(tool, ()):
            if prereq not in completed:
                pinned.add(prereq)
    return pinned


def discovery_tools_for_task(task: str, completed: set[str]) -> set[str]:
    """Backward-compatible alias — prerequisites from completed hops only."""
    _ = task
    return discovery_tools_for_completed(completed)


def suggest_chain_calls(
    task: str,
    hops: list[dict[str, Any]],
    *,
    bound_tools: set[str],
) -> list[dict[str, Any]]:
    """Suggest the next discovery hop before a detail tool."""
    from app.orchestration.tooling.tool_harvest import discovered_entities

    suggestions: list[dict[str, Any]] = []
    ok = _ok_tool_names(hops)
    discovered = discovered_entities(hops)
    likely = likely_tools_for_task(task) & bound_tools

    for target in sorted(likely):
        if target in ok:
            continue
        prereqs = TOOL_PREREQUISITES.get(target, ())
        for prereq in prereqs:
            if prereq not in bound_tools or prereq in ok:
                continue
            args: dict[str, Any] = {}
            projects = discovered.get("projects") or []
            if projects:
                args["projects"] = ",".join(projects[:6])
                args["project"] = projects[0]
            suggestions.append({
                "tool": prereq,
                "args": args,
                "reason": (
                    f"Run {prereq} before {target} — detail tools need ids/versions "
                    "from discovery results in prior_hops.",
                ),
            })
            break
    return suggestions[:4]
