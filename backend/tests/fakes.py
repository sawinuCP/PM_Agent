"""Shared fakes for component/integration tests — no network, no live LLM.

FakeMCPManager mirrors the MCPManager surface consumed by the factory, graph
builder and planner (bind/call/sensitivity/catalog/health) and emits the same
typed tool events, so orchestration tests exercise the real code paths.
"""

from __future__ import annotations

import json
from typing import Any

from app.mcp_integration.digest import build_digest, structured_preview
from app.mcp_integration.manager import _tool_finished_event, _tool_started_event
from app.models.schemas import AgentTemplate

CANNED_RESULTS: dict[str, Any] = {
    "jira_get_sprints": {
        "sprints": [
            {"id": f"SPR-{i}", "name": f"Sprint {i}", "state": "closed",
             "completed_points": p}
            for i, p in zip(range(1, 6), [30, 42, 38, 45, 36], strict=False)
        ],
        "count": 5,
    },
    "jira_get_sprint_issues": {
        "sprint_id": "SPR-6",
        "issues": [
            {"id": "PLAT-9", "summary": "Stabilize checkout retries", "type": "Bug",
             "status": "In Progress", "story_points": 3, "assignee": "bob",
             "sprint": "SPR-6"},
            {"id": "PLAT-10", "summary": "Add sprint health dashboard", "type": "Story",
             "status": "In Progress", "story_points": 5, "assignee": "alice",
             "sprint": "SPR-6"},
            {"id": "PLAT-11", "summary": "Document incident handover", "type": "Task",
             "status": "To Do", "story_points": 2, "assignee": "erin",
             "sprint": "SPR-6"},
        ],
        "count": 3,
    },
    "jira_search": {
        "issues": [
            {"id": "PLAT-9", "summary": "Stabilize checkout retries", "type": "Bug",
             "status": "In Progress", "story_points": 3, "assignee": "bob",
             "sprint": "SPR-6"},
            {"id": "PLAT-10", "summary": "Add sprint health dashboard", "type": "Story",
             "status": "In Progress", "story_points": 5, "assignee": "alice",
             "sprint": "SPR-6"},
            {"id": "PLAT-11", "summary": "Document incident handover", "type": "Task",
             "status": "To Do", "story_points": 2, "assignee": "erin",
             "sprint": "SPR-6"},
        ],
        "count": 3,
        "total_matched": 3,
    },
    "run_python": {"stdout": "", "result": "{'method': 'moving_average_3', 'forecast': 40.3}",
                   "ms": 3, "status": "ok"},
    "jira_get_project_health": {
        "project": "PLAT", "project_name": "Platform Delivery",
        "assessment": "derived_from_project_data",
        "overall": "At Risk", "schedule": "At Risk",
        "dimensions": {"schedule": "At Risk", "scope": "Elevated",
                       "engineering": "Elevated", "bugs": "At Risk",
                       "dependencies": "At Risk", "overall": "At Risk"},
        "evidence": {"open_issues": 10, "blocked_issues": 2, "overdue_issues": 1,
                     "remaining_story_points": 18, "days_remaining_in_sprint": 9},
    },
    "jira_get_blocked_issues": {
        "issues": [
            {"id": "PLAT-9", "summary": "Stabilize checkout retries",
             "blocked_by": ["PLAT-13"], "assignee": "bob"},
        ],
        "count": 1, "project": "PLAT",
    },
    "jira_get_releases": {
        "project": "PLAT",
        "releases": [{"name": "2.5.0", "released": False, "release_date": "2026-09-18"}],
        "count": 1,
    },
    "jira_get_release_status": {
        "release": {"name": "2.5.0", "released": False, "release_date": "2026-09-18"},
        "project": "PLAT", "completion_percent": 12.5,
        "issue_counts": {"total": 8, "done": 0, "open": 8, "open_bugs": 3,
                         "blocked": 2, "overdue": 1},
        "open_issues": [{"id": "PLAT-9", "summary": "Stabilize checkout retries"}],
        "unresolved_bugs": [{"id": "PLAT-14", "summary": "Webhook signature mismatch"}],
    },
    "jira_get_team_workload": {
        "workload": [
            {"assignee": "bob", "open_issues": 2, "remaining_points": 6, "blocked": 1},
            {"assignee": "alice", "open_issues": 2, "remaining_points": 7, "blocked": 0},
        ],
        "count": 2, "project": "PLAT",
    },
    "jira_get_overdue_issues": {
        "issues": [{"id": "PLAT-14", "summary": "Webhook signature mismatch",
                    "due_date": "2026-09-03"}],
        "count": 1, "as_of": "2026-09-05",
    },
    "jira_get_sprint_report": {
        "sprint": {"id": "SPR-6", "name": "Sprint 6", "state": "active"},
        "days_remaining": 9, "issue_points_remaining": 23,
        "assessment_inputs": {"remaining_issue_count": 7, "blocked_count": 2,
                              "overdue_count": 1, "added_after_start_count": 1},
    },
    "jira_get_dependencies": {
        "links": [{"type": "blocks", "from": "PLAT-13", "to": "PLAT-9",
                   "from_done": False, "to_status": "In Progress"}],
        "count": 1, "project": "PLAT",
    },
    "jira_get_projects": {
        "projects": [
            {"key": "PLAT", "name": "Platform Delivery"},
            {"key": "NOVA", "name": "Nova Mobile Platform"},
        ],
        "count": 2,
    },
    "jira_create_issue": {
        "issue_id": "PLAT-42",
        "project": "PLAT",
        "summary": "Follow-up task",
        "status": "To Do",
    },
}

JIRA_TOOL_NAMES = (
    "jira_search", "jira_get_issue", "jira_get_sprints", "jira_get_sprint",
    "jira_get_sprint_issues", "jira_get_sprint_report", "jira_get_board",
    "jira_get_worklogs", "jira_get_issue_comments", "jira_get_issue_history",
    "jira_get_projects", "jira_get_project", "jira_get_project_health",
    "jira_get_users", "jira_get_team_members", "jira_get_team_workload",
    "jira_get_releases", "jira_get_release_status", "jira_get_blocked_issues",
    "jira_get_overdue_issues", "jira_get_stuck_issues", "jira_get_dependencies",
    "jira_get_epics", "jira_get_backlog", "jira_get_recent_activity",
    "jira_create_issue", "jira_update_issue", "jira_transition_issue",
    "jira_add_comment",
)

CATALOG: dict[str, list[dict[str, Any]]] = {
    "jira": [
        {"name": name, "description": name, "input_schema": {"type": "object"},
         "sensitive": name in {"jira_create_issue", "jira_update_issue",
                               "jira_transition_issue", "jira_add_comment"}}
        for name in JIRA_TOOL_NAMES
    ],
    "code-sandbox": [
        {"name": "run_python", "description": "Execute Python",
         "input_schema": {"type": "object"}, "sensitive": False},
    ],
}


class FakeMCPManager:
    """Deterministic stand-in for the MCP Manager."""

    def __init__(self, *, fail_tools: set[str] | None = None,
                 sensitive_tools: set[str] | None = None,
                 servers_down: set[str] | None = None) -> None:
        self.fail_tools = fail_tools or set()
        self.sensitive_tools = sensitive_tools or set()
        self.servers_down = servers_down or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counter = 0

    async def start(self) -> None:  # parity with the real manager
        return None

    async def shutdown(self) -> None:
        return None

    def tool_catalog(self) -> dict[str, list[dict[str, Any]]]:
        return CATALOG

    async def health(self) -> dict[str, dict[str, Any]]:
        servers = ("jira", "code-sandbox")
        out: dict[str, dict[str, Any]] = {}
        for name in servers:
            if name == "code-sandbox":
                out[name] = {
                    "transport": "local",
                    "status": "down" if name in self.servers_down else "up",
                }
            else:
                out[name] = {
                    "transport": "streamable_http",
                    "status": "down" if name in self.servers_down else "up",
                }
        return out

    def unavailable_servers(self) -> list[str]:
        return sorted(self.servers_down)

    def is_tool_sensitive(self, tool_name: str) -> bool:
        if tool_name in self.sensitive_tools:
            return True
        for catalog in CATALOG.values():
            for entry in catalog:
                if entry.get("name") == tool_name:
                    return bool(entry.get("sensitive"))
        return False

    async def bind_toolset(self, template: AgentTemplate, step_id: str = "unknown",
                           run_id: str = "", emit=None,
                           requested_tools: list[str] | None = None) -> list[Any]:
        """Fake counterpart of MCPManager.bind_toolset (same narrowing rules)."""
        from langchain_core.tools import Tool

        allowed_servers = set(template.allowed_mcp_servers)
        if requested_tools is not None:
            requested = set(requested_tools) & set(template.allowed_tools)
        else:
            requested = set(template.allowed_tools)
        scoped: dict[str, dict[str, Any]] = {}
        for server_name, tools in CATALOG.items():
            if allowed_servers and server_name not in allowed_servers:
                continue
            for t in tools:
                scoped[t["name"]] = t
        bound: list[Any] = []
        manager = self
        for tool_name in template.allowed_tools:
            if tool_name not in requested or tool_name not in scoped:
                continue
            entry = scoped[tool_name]

            def _make(mgr=manager, name=tool_name, tmpl=template, step=step_id,
                      rid=run_id, on_event=emit):
                async def _call(**kwargs: Any) -> str:
                    outcome = await mgr.call_tool_for_agent(
                        tool_name=name, args=kwargs, agent_template=tmpl,
                        step_id=step, run_id=rid, emit=on_event)
                    return outcome["digest"]
                return _call

            bound.append(Tool.from_function(func=None, name=tool_name,
                                            description=f"fake {entry['name']}",
                                            coroutine=_make()))
        return bound

    async def call_tool_for_agent(self, *, tool_name: str, args: dict[str, Any],
                                  agent_template: AgentTemplate, step_id: str,
                                  run_id: str = "", emit=None, mode: str = "") -> dict[str, Any]:
        if tool_name not in agent_template.allowed_tools:
            from app.core.errors import MCPToolPermissionError

            raise MCPToolPermissionError(f"tool '{tool_name}' not allowed")
        self.calls.append((tool_name, args))
        self._counter += 1
        result_id = f"fake-{self._counter}"
        status = "error" if tool_name in self.fail_tools else "ok"
        payload = ({"error": "injected failure"} if status == "error"
                   else CANNED_RESULTS.get(tool_name, {"ok": True}))
        digest = build_digest(payload)
        if emit is not None:
            await emit(_tool_started_event(
                run_id, step_id, tool_name, dict(args), mode=mode))
            await emit(_tool_finished_event(run_id, step_id, tool_name, status, 1,
                                            digest, result_id, mode=mode))
        return {
            "digest": digest,
            "preview": structured_preview(payload),
            "result_id": result_id,
            "status": status,
            "ms": 1,
        }


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str)
