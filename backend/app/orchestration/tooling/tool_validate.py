"""Pre-flight validation for tool calls (schema + task scope)."""

from __future__ import annotations

from typing import Any

from app.orchestration.coverage.entity_scope import step_scope_entities
from app.orchestration.utils.schema_utils import required_schema_fields


def validate_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    schema: dict[str, Any] | None,
    task: str,
    goal: str,
) -> str | None:
    """Return rejection reason, or None if the call may proceed."""
    clean = dict(args or {})
    for field in required_schema_fields(schema):
        value = clean.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"missing required arg '{field}' for {tool_name}"

    scope = step_scope_entities(task=task, goal=goal)
    if scope and tool_name in {
        "jira_get_project_health",
        "jira_get_project",
        "jira_get_blocked_issues",
        "jira_get_overdue_issues",
    }:
        project = clean.get("project")
        projects = clean.get("projects")
        if isinstance(project, str) and project.strip():
            if project.strip().upper() not in scope:
                return (
                    f"project '{project}' not in step scope {scope} — "
                    f"use the project key from the assigned task"
                )
        elif isinstance(projects, str) and projects.strip():
            keys = {part.strip().upper() for part in projects.split(",") if part.strip()}
            if keys and not keys <= set(scope):
                return f"projects '{projects}' exceed step scope {scope}"
        elif len(scope) == 1 and not project and not projects:
            return (
                f"{tool_name} needs project='{scope[0]}' for this step scope"
            )

    if tool_name == "jira_get_sprint_issues" and not clean.get("sprint_id"):
        return "jira_get_sprint_issues requires sprint_id — call jira_get_sprints first"

    if tool_name == "jira_get_release_status":
        if not clean.get("version"):
            return "jira_get_release_status requires version — call jira_get_releases first"
        if scope and clean.get("project"):
            if str(clean["project"]).upper() not in scope:
                return f"release status project must match step scope {scope}"

    placeholders = ("<", "TBD", "unknown", "pick from", "placeholder")
    for key, value in clean.items():
        text = str(value).lower()
        if any(marker in text for marker in placeholders):
            return f"placeholder value in arg '{key}' — use concrete ids from prior_hops"

    return None
