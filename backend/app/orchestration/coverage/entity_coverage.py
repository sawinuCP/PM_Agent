"""Per-entity evidence gates — block finish until each named project has data."""

from __future__ import annotations

from typing import Any

from app.mcp_integration.digest import decode_step_packet
from app.orchestration.coverage.entity_scope import project_keys_in_text, step_scope_entities
from app.orchestration.coverage.task_patterns import (
    is_blocked_task,
    is_bug_count_task,
    is_health_task,
    is_sprint_metrics_task,
    last_n_sprints_from_text,
    task_text,
)
from app.orchestration.utils.hop_utils import ok_hops

_HEALTH_TOOLS = frozenset({
    "jira_get_project_health",
    "jira_get_project",
})
_BLOCKED_TOOLS = frozenset({"jira_get_blocked_issues"})
_SPRINT_METRICS_TOOLS = frozenset({"jira_get_sprint_report"})
_BUG_EVIDENCE_TOOLS = frozenset({
    "jira_get_sprint_issues",
    "jira_get_sprint_report",
    "jira_get_sprints",
    "jira_get_sprint",
})


def _project_from_hop(hop: dict[str, Any]) -> str | None:
    args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
    project = args.get("project") or args.get("projects")
    if isinstance(project, str) and project.strip():
        first = project.split(",")[0].strip().upper()
        return first if first else None
    data = hop.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("project"), str):
            return data["project"].strip().upper()
        if isinstance(data.get("projects"), list):
            for row in data["projects"]:
                if isinstance(row, dict) and row.get("project"):
                    return str(row["project"]).strip().upper()
                if isinstance(row, dict) and row.get("key"):
                    return str(row["key"]).strip().upper()
    return None


def sprint_id_to_project(hops: list[dict[str, Any]]) -> dict[str, str]:
    """Map sprint ids discovered under a scoped project sprints hop."""
    mapping: dict[str, str] = {}
    for hop in ok_hops(hops):
        if hop.get("tool") != "jira_get_sprints":
            continue
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        project = args.get("project")
        if not isinstance(project, str) or not project.strip():
            continue
        key = project.strip().upper()
        data = hop.get("data")
        if not isinstance(data, dict):
            continue
        for sprint in data.get("sprints") or []:
            if isinstance(sprint, dict) and sprint.get("id"):
                mapping[str(sprint["id"]).strip()] = key
    return mapping


def has_sprints_hop_for_project(hops: list[dict[str, Any]], project: str) -> bool:
    want = project.strip().upper()
    for hop in ok_hops(hops):
        if hop.get("tool") != "jira_get_sprints":
            continue
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        got = args.get("project")
        if isinstance(got, str) and got.strip().upper() == want:
            return True
    return False


def entities_with_health_evidence(hops: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _HEALTH_TOOLS:
            continue
        key = _project_from_hop(hop)
        if key:
            found.add(key)
        data = hop.get("data")
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            for row in data["projects"]:
                if isinstance(row, dict):
                    proj = row.get("project") or row.get("key")
                    if isinstance(proj, str) and proj.strip():
                        found.add(proj.strip().upper())
    return found


def entities_with_blocked_evidence(hops: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _BLOCKED_TOOLS:
            continue
        key = _project_from_hop(hop)
        if key:
            found.add(key)
    return found


def entities_with_sprint_metrics_evidence(hops: list[dict[str, Any]]) -> set[str]:
    sprint_projects = sprint_id_to_project(hops)
    found: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _SPRINT_METRICS_TOOLS:
            continue
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        sprint_id = args.get("sprint_id")
        if isinstance(sprint_id, str) and sprint_id.strip():
            project = sprint_projects.get(sprint_id.strip())
            if project:
                found.add(project)
    return found


def _hop_has_bug_metrics(hop: dict[str, Any]) -> bool:
    data = hop.get("data")
    if not isinstance(data, dict):
        return False
    if data.get("comparison") and isinstance(data["comparison"], dict):
        bugs = data["comparison"].get("bug_counts")
        if isinstance(bugs, list) and bugs:
            return True
    for row in data.get("by_sprint") or []:
        if isinstance(row, dict) and "bug_count" in row:
            return True
    for row in data.get("sprints") or []:
        if isinstance(row, dict) and "bug_count" in row:
            return True
    if "bug_count" in data or "type_counts" in data:
        return True
    return False


def entities_with_bug_evidence(hops: list[dict[str, Any]]) -> set[str]:
    """Projects/boards with per-sprint bug metrics in successful hops."""
    sprint_projects = sprint_id_to_project(hops)
    found: set[str] = set()
    for hop in ok_hops(hops):
        if hop.get("tool") not in _BUG_EVIDENCE_TOOLS:
            continue
        if not _hop_has_bug_metrics(hop):
            continue
        key = _project_from_hop(hop)
        if key:
            found.add(key)
            continue
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        sprint_id = args.get("sprint_id")
        if isinstance(sprint_id, str) and sprint_id.strip():
            project = sprint_projects.get(sprint_id.strip())
            if project:
                found.add(project)
        data = hop.get("data")
        if isinstance(data, dict):
            for sprint in data.get("sprints") or []:
                if isinstance(sprint, dict) and sprint.get("board_id"):
                    for hop2 in ok_hops(hops):
                        if hop2.get("tool") != "jira_get_projects":
                            continue
                        payload = hop2.get("data")
                        if not isinstance(payload, dict):
                            continue
                        for proj in payload.get("projects") or []:
                            if isinstance(proj, dict) and proj.get("board_id") == sprint.get("board_id"):
                                key2 = str(proj.get("key") or "").strip().upper()
                                if key2:
                                    found.add(key2)
    return found


def bug_metrics_present(hops: list[dict[str, Any]]) -> bool:
    """True when hops contain per-sprint bug counts (with or without project key)."""
    if entities_with_bug_evidence(hops):
        return True
    return any(
        hop.get("tool") in _BUG_EVIDENCE_TOOLS and _hop_has_bug_metrics(hop)
        for hop in ok_hops(hops)
    )


def entity_finish_blocked_reason(
    *,
    task: str,
    goal: str,
    hops: list[dict[str, Any]],
    upstream_hops: list[dict[str, Any]] | None = None,
) -> str | None:
    """Block finish when step names project(s) but scoped evidence is missing."""
    _ = upstream_hops  # upstream merge removed — judge current-step hops only
    text = task_text(task=task, goal=goal)
    required = step_scope_entities(task=task, goal=goal)

    if not required and not is_bug_count_task(text):
        return None

    if is_blocked_task(text):
        got = entities_with_blocked_evidence(hops)
        missing = [key for key in required if key not in got]
        if missing:
            return (
                f"Step scope requires blocked-issue evidence for {missing} — "
                f"call jira_get_blocked_issues(project=<KEY>) for each missing project "
                f"before finishing. Got evidence for: {sorted(got) or 'none'}."
            )
        return None

    if is_sprint_metrics_task(text):
        got = entities_with_sprint_metrics_evidence(hops)
        missing = [key for key in required if key not in got]
        if missing:
            return (
                f"Step scope requires sprint metrics for {missing} — "
                f"call jira_get_sprints(project=<KEY>, state=active) then "
                f"jira_get_sprint_report(sprint_id=<ID>) before finishing. "
                f"Got evidence for: {sorted(got) or 'none'}."
            )
        return None

    if is_bug_count_task(text):
        got = entities_with_bug_evidence(hops)
        if not got:
            n = last_n_sprints_from_text(text)
            return (
                f"Step requires per-sprint bug counts — call "
                f"jira_get_sprints(project=<KEY>, state=closed, last_n={n}, "
                f"include_metrics=true) or jira_get_sprint_report(last_n={n}) "
                f"before finishing. Got bug evidence for: none."
            )
        return None

    if is_health_task(text):
        got = entities_with_health_evidence(hops)
        missing = [key for key in required if key not in got]
        if missing:
            return (
                f"Step scope requires health evidence for {missing} — "
                f"call jira_get_project_health(project=<KEY>) for each missing project "
                f"before finishing. Got evidence for: {sorted(got) or 'none'}."
            )
    return None


def synthesis_entity_gaps(goal: str, step_outputs: dict[str, str]) -> list[str]:
    """Projects named in goal but absent from successful scoped tool hops."""
    required = project_keys_in_text(goal)
    if len(required) < 2:
        return []

    text = goal.lower()
    if "blocked" in text:
        evidence_fn = entities_with_blocked_evidence
    elif any(word in text for word in (
        "sprint progress", "active sprint", "committed", "days remaining",
    )):
        evidence_fn = entities_with_sprint_metrics_evidence
    else:
        evidence_fn = entities_with_health_evidence

    found: set[str] = set()
    for step_id, raw in step_outputs.items():
        packet = decode_step_packet(raw, step_id)
        found |= evidence_fn(packet.get("hops") or [])
    return [key for key in required if key not in found]
