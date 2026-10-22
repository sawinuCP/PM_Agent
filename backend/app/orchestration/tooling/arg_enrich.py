"""Fill tool args from prior hop discoveries (schema-aware, conservative)."""

from __future__ import annotations

from typing import Any

from app.orchestration.tooling.tool_harvest import discovered_entities


def enrich_tool_args(
    tool_name: str,
    args: dict[str, Any],
    hops: list[dict[str, Any]],
    *,
    task: str = "",
) -> dict[str, Any]:
    """Merge discovered ids into args when required fields are missing."""
    enriched = dict(args or {})
    discovered = discovered_entities(hops)
    projects = discovered.get("projects") or []
    sprints = discovered.get("sprints") or []
    boards = discovered.get("boards") or []

    if tool_name == "jira_get_sprint_issues":
        if not enriched.get("sprint_id") and sprints:
            enriched["sprint_id"] = sprints[0]
        if not enriched.get("project") and projects:
            enriched["project"] = projects[0]

    if tool_name in {"jira_get_sprints", "jira_get_sprint_report"}:
        if not enriched.get("project") and not enriched.get("projects") and projects:
            if len(projects) == 1:
                enriched["project"] = projects[0]
            else:
                enriched["projects"] = ",".join(projects[:8])
        if not enriched.get("board_id") and boards and len(boards) == 1:
            enriched["board_id"] = boards[0]

    if tool_name == "jira_get_sprint_report" and not enriched.get("sprint_id"):
        for hop in reversed(hops):
            if hop.get("type") != "tool_result" or hop.get("status") != "ok":
                continue
            if hop.get("tool") != "jira_get_sprints":
                continue
            data = hop.get("data")
            if not isinstance(data, dict):
                continue
            sprints = data.get("sprints") or []
            active = [
                row for row in sprints
                if isinstance(row, dict) and row.get("state") == "active"
            ]
            pick = active[0] if active else (sprints[0] if sprints else None)
            if isinstance(pick, dict) and pick.get("id"):
                enriched["sprint_id"] = str(pick["id"]).strip()
                break

    if tool_name == "jira_get_release_status":
        if not enriched.get("project") and projects:
            enriched["project"] = projects[0]

    if tool_name in {
        "jira_get_project_health",
        "jira_get_blocked_issues",
        "jira_get_team_workload",
        "jira_get_overdue_issues",
    }:
        if not enriched.get("project") and not enriched.get("projects") and projects:
            if len(projects) == 1:
                enriched["project"] = projects[0]
            else:
                enriched["projects"] = ",".join(projects[:8])

    if tool_name == "jira_get_sprint" and not enriched.get("sprint_id") and sprints:
        enriched["sprint_id"] = sprints[0]

    return enriched
