"""Jira MCP server (streamable-http) wrapping the local jira_service simulator.

Local demonstration server: MCP tools wrap a Jira-like project-management
service seeded with a realistic dummy world.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server import MCPServer

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from mcp_servers.jira import service as svc  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira-simulator")

mcp = MCPServer("jira-simulator")

def reset_state() -> None:
    """Re-seed to the deterministic snapshot (demo reproducibility)."""
    svc.reset_state()


@mcp.tool()
def jira_get_board(board_id: str = "", board_ids: str = "") -> dict:
    """Get board configuration (project, type) by board id. Pass board_ids
    (comma-separated) to fetch several boards in one call. Maps to Jira Agile
    GET /board/{id}."""
    return svc.get_board(board_id, board_ids)


@mcp.tool()
def jira_get_sprints(board_id: str = "", state: str = "closed",
                     project: str = "", last_n: int = 0,
                     include_metrics: bool = False, board_ids: str = "",
                     projects: str = "") -> dict:
    """List sprints by state (closed/active/future). Optional last_n keeps the
    newest N sprints (use for 'last 11 sprints'). Set include_metrics=true to
    attach per-sprint issue/bug/point counts in this same call — do not fan out
    get_sprint_issues just to count bugs. board_id / board_ids and
    project / projects are optional filters."""
    return svc.get_sprints(
        board_id, state, project, last_n, include_metrics, board_ids, projects)


@mcp.tool()
def jira_get_sprint(sprint_id: str = "", sprint_ids: str = "",
                    last_n: int = 0) -> dict:
    """Sprint detail plus status counts and remaining points. Pass sprint_id for
    one sprint, or sprint_ids (comma-separated) / last_n for a batch."""
    return svc.get_sprint(sprint_id, sprint_ids, last_n)


@mcp.tool()
def jira_get_sprint_issues(sprint_id: str = "", max_results: int = 50,
                           start_at: int = 0, sprint_ids: str = "",
                           last_n: int = 0, issue_type: str = "",
                           state: str = "closed") -> dict:
    """Issues for one or many sprints in a single call. Prefer last_n (e.g. 11)
    or sprint_ids='SPR-1,SPR-2' over repeating this tool. Optional issue_type
    (Bug) filters. Batch responses include by_sprint totals so you do not need
    one call per sprint. Single sprint_id keeps the original paginated shape."""
    return svc.get_sprint_issues(
        sprint_id, max_results, start_at, sprint_ids, last_n, issue_type, state)


@mcp.tool()
def jira_get_sprint_report(sprint_id: str = "", sprint_ids: str = "",
                          last_n: int = 0) -> dict:
    """Sprint report: remaining vs done points, blockers, overdue items.
    Pass last_n or sprint_ids to compare many sprints in one call (returns
    reports[] + comparison). Single sprint_id keeps the full issue lists."""
    return svc.get_sprint_report(sprint_id, sprint_ids, last_n)


@mcp.tool()
def jira_search(jql: str, max_results: int = 20, start_at: int = 0) -> dict:
    """Search issues with a validated JQL subset: project, status, type, sprint,
    assignee, priority, epic, labels, component, fixVersion. AND + ORDER BY
    (ignored). Server-side max_results cap. Maps to Jira issue search / JQL."""
    return svc.search_issues(jql, max_results, start_at)


@mcp.tool()
def jira_get_issue(issue_id: str = "", issue_ids: str = "") -> dict:
    """Issue detail. Pass issue_id for one ticket or issue_ids (comma-separated)
    for a compact batch. Do not call this once per key when several keys are known."""
    return svc.get_issue(issue_id, issue_ids)


@mcp.tool()
def jira_get_worklogs(issue_id: str = "", issue_ids: str = "") -> dict:
    """Time-tracking entries. issue_ids batches several issues in one call."""
    return svc.get_worklogs(issue_id, issue_ids)


@mcp.tool()
def jira_get_issue_comments(issue_id: str = "", issue_ids: str = "") -> dict:
    """Comments. issue_ids batches several issues in one call."""
    return svc.get_issue_comments(issue_id, issue_ids)


@mcp.tool()
def jira_get_issue_history(issue_id: str = "", issue_ids: str = "") -> dict:
    """Changelog / activity. issue_ids batches several issues in one call."""
    return svc.get_issue_history(issue_id, issue_ids)




@mcp.tool()
def jira_get_projects() -> dict:
    """List all projects with lead, open-issue counts, and category. Use first
    when the user has not named a project. Maps to GET /project."""
    return svc.get_projects()


@mcp.tool()
def jira_get_project(project: str = "", projects: str = "") -> dict:
    """Get one or many projects: metadata, members, active sprint, releases,
    and issue counts. Pass projects='PLAT,NOVA' for a batch (items[]). Accepts
    key (PLAT) or name fragment (Nova, Platform)."""
    return svc.get_project(project, projects)


@mcp.tool()
def jira_get_project_health(project: str = "", projects: str = "") -> dict:
    """Derived project-health assessment (schedule, scope, engineering, bugs,
    dependencies). Evidence is counted from issues/sprints/releases — not invented.
    Omit project for every project, or pass projects='PLAT,NOVA' for a batch.
    Use for 'how is the project', 'are we on track', or 'why at risk'."""
    return svc.get_project_health(project, projects)


@mcp.tool()
def jira_get_users(project: str = "", projects: str = "") -> dict:
    """Directory of people. Optional project / projects limits to those members."""
    return svc.get_users(project or None, projects)


@mcp.tool()
def jira_get_team_members(project: str = "", projects: str = "") -> dict:
    """Members of one or many projects with display name, role, and team."""
    return svc.get_team_members(project, projects)


@mcp.tool()
def jira_get_team_workload(project: str = "", sprint_id: str = "",
                          projects: str = "", sprint_ids: str = "",
                          last_n: int = 0) -> dict:
    """Open-work load by assignee: remaining points, blocked, overdue, in-progress.
    Pass projects, sprint_ids, or last_n to cover several scopes in one call.
    Use for overload, idle engineers, or 'who is working on what'."""
    return svc.get_team_workload(
        project or None, sprint_id or None, projects, sprint_ids, last_n)


@mcp.tool()
def jira_get_releases(project: str = "", projects: str = "") -> dict:
    """List versions/releases for one or many projects. Maps to project versions API."""
    return svc.get_releases(project, projects)


@mcp.tool()
def jira_get_release_status(project: str = "", version: str = "",
                           versions: str = "", projects: str = "") -> dict:
    """Release progress: completion percent, open/blocked/overdue issues and bugs
    targeting that version. Pass versions='2.5.0,3.1.0' to compare several
    releases in one call. Use for 'how close is 2.5' or 'what blocks the release'."""
    return svc.get_release_status(project, version, versions, projects)


@mcp.tool()
def jira_get_blocked_issues(project: str = "", projects: str = "") -> dict:
    """Open issues that still have unresolved blockers (issue links).
    Pass projects for a by_project[] batch."""
    return svc.get_blocked_issues(project or None, projects)


@mcp.tool()
def jira_get_overdue_issues(project: str = "", projects: str = "") -> dict:
    """Open issues whose due date is before the simulation clock (2026-09-05).
    Pass projects for a by_project[] batch."""
    return svc.get_overdue_issues(project or None, projects)


@mcp.tool()
def jira_get_stuck_issues(project: str = "", days: int = 7,
                         projects: str = "") -> dict:
    """In-progress issues not updated for at least `days` (stale / stuck work).
    Pass projects for a by_project[] batch."""
    return svc.get_stuck_issues(project or None, days, projects)


@mcp.tool()
def jira_get_dependencies(issue_id: str = "", project: str = "",
                         issue_ids: str = "", projects: str = "") -> dict:
    """Issue-link dependencies. Pass issue_id / issue_ids for ticket links,
    or project / projects for blocking graphs. Maps to issue links."""
    return svc.get_dependencies(
        issue_id or None, project or None, issue_ids, projects)


@mcp.tool()
def jira_get_epics(project: str = "", projects: str = "") -> dict:
    """Epics in one or many projects with child issues and completion counts."""
    return svc.get_epics(project, projects)


@mcp.tool()
def jira_get_backlog(project: str = "", max_results: int = 20, start_at: int = 0,
                    projects: str = "") -> dict:
    """Unscheduled (backlog) issues for one or many projects, excluding epics.
    Paginated. Maps to Agile board backlog."""
    return svc.get_backlog(project, max_results, start_at, projects)


@mcp.tool()
def jira_get_recent_activity(project: str = "", days: int = 7,
                             max_results: int = 20, projects: str = "") -> dict:
    """Recent comments and changelog events. Use for 'what changed this week'.
    Pass projects for several projects in one call."""
    return svc.get_recent_activity(project or None, days, max_results, projects)


@mcp.tool()
def jira_create_issue(project: str, issue_type: str, summary: str) -> dict:
    """[SENSITIVE - requires approval] Create an issue. Requires a valid project
    key and issue type; visibly mutates state that jira_search then returns."""
    result = svc.create_issue(project, issue_type, summary)
    if result.get("created"):
        logger.info("created %s", result["issue"]["id"])
    return result


@mcp.tool()
def jira_update_issue(issue_id: str, summary: str) -> dict:
    """[SENSITIVE - requires approval] Update an issue's summary."""
    return svc.update_issue(issue_id, summary)


@mcp.tool()
def jira_transition_issue(issue_id: str, status: str) -> dict:
    """[SENSITIVE - requires approval] Move an issue to a new status. Validates
    the transition against the legal state machine (illegal -> structured error)."""
    return svc.transition_issue(issue_id, status)


@mcp.tool()
def jira_add_comment(issue_id: str, body: str) -> dict:
    """[SENSITIVE - requires approval] Add a comment to an issue."""
    return svc.add_comment(issue_id, body)


@mcp.tool()
def jira_reset_fixtures() -> dict:
    """Reset the simulator to its deterministic seed (demo reproducibility)."""
    return svc.reset_fixtures()


if __name__ == "__main__":
    host = os.environ.get("JIRA_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("JIRA_MCP_PORT", "8001"))
    logger.info("jira-simulator listening on %s:%s", host, port)
    mcp.run(transport="streamable-http", host=host, port=port)
