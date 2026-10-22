"""Jira-like project-management service used by the local MCP server.

The AI layer talks only through MCP tools. This module is the data/query
boundary those tools wrap — the same surface can later call Jira REST.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, timedelta
from typing import Any

from mcp_servers.jira.dataset import AS_OF, build_world

MAX_RESULTS_CAP = 100
LAST_N_MAX = 20
PER_SPRINT_ISSUE_CAP = 12
TOTAL_ISSUE_CAP = 80
DONE_STATUSES = {"Done", "Closed"}
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "To Do": {"In Progress"},
    "In Progress": {"In Review", "Done", "To Do"},
    "In Review": {"Done", "In Progress"},
    "Done": set(),
}
CREATE_TYPES = {"Story", "Bug", "Task", "Spike", "Epic", "Sub-task"}
JQL_FIELDS = {
    "project", "status", "type", "sprint", "assignee", "priority", "epic",
    "labels", "component", "fixversion", "version", "summary", "text",
}

_LOCK = threading.Lock()
USERS: dict[str, dict[str, Any]] = {}
PROJECTS: dict[str, dict[str, Any]] = {}
BOARDS: dict[str, dict[str, Any]] = {}
SPRINTS: dict[str, dict[str, Any]] = {}
VERSIONS: list[dict[str, Any]] = []
ISSUES: list[dict[str, Any]] = []
WORKLOGS: dict[str, list[dict[str, Any]]] = {}


def _as_of() -> date:
    return AS_OF


def reset_state() -> None:
    """Re-seed the deterministic snapshot (demo reproducibility)."""
    world = build_world()
    USERS.clear(); USERS.update(world["users"])
    PROJECTS.clear(); PROJECTS.update(world["projects"])
    BOARDS.clear(); BOARDS.update(world["boards"])
    SPRINTS.clear(); SPRINTS.update(world["sprints"])
    VERSIONS.clear(); VERSIONS.extend(world["versions"])
    ISSUES.clear(); ISSUES.extend(world["issues"])
    WORKLOGS.clear(); WORKLOGS.update(world["worklogs"])


reset_state()


def _need(value: str | None, name: str) -> tuple[str, dict[str, Any] | None]:
    """Return (stripped value, None) or ('', error payload) if blank."""
    text = (value or "").strip()
    if not text:
        return "", {"error": f"{name} is required"}
    return text, None


def _cap(max_results: int, default: int = 20) -> int:
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_RESULTS_CAP))


def _start(start_at: int) -> int:
    try:
        n = int(start_at)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def _is_done(issue: dict[str, Any]) -> bool:
    return issue.get("status") in DONE_STATUSES


def _is_overdue(issue: dict[str, Any], today: date | None = None) -> bool:
    if _is_done(issue):
        return False
    due = issue.get("due_date")
    if not due:
        return False
    try:
        return date.fromisoformat(str(due)) < (today or _as_of())
    except ValueError:
        return False


def _days_stale(issue: dict[str, Any], today: date | None = None) -> int:
    raw = issue.get("updated") or issue.get("created") or ""
    try:
        updated = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return 0
    return max(0, ((today or _as_of()) - updated).days)


def _user_view(account_id: str) -> dict[str, Any]:
    profile = USERS.get(account_id) or {
        "account_id": account_id, "display_name": account_id,
        "role": "", "team": "",
    }
    return dict(profile)


def parse_id_list(value: Any) -> list[str]:
    """Accept a list, JSON array string, or comma-separated ids."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;|]", text) if part.strip()]


def _merge_id_fields(*values: Any) -> list[str]:
    """Merge singular and plural id tool args (LLMs often comma-pack the singular field)."""
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in parse_id_list(value):
            if part not in seen:
                seen.add(part)
                merged.append(part)
    return merged


def _as_last_n(value: Any) -> int:
    try:
        return max(0, min(int(value), LAST_N_MAX))
    except (TypeError, ValueError):
        return 0


def _sprint_metrics(sprint_id: str) -> dict[str, Any]:
    issues = [compact_issue(item) for item in ISSUES if item.get("sprint") == sprint_id]
    type_counts: dict[str, int] = {}
    for card in issues:
        kind = str(card.get("type") or "Unknown")
        type_counts[kind] = type_counts.get(kind, 0) + 1
    return {
        "issue_count": len(issues),
        "bug_count": type_counts.get("Bug", 0),
        "type_counts": type_counts,
        "completed_story_points": sum(c["story_points"] for c in issues if c["is_done"]),
        "remaining_story_points": sum(c["story_points"] for c in issues if not c["is_done"]),
        "status_counts": _status_counts(issues),
    }


def _select_sprint_ids(*, sprint_id: str = "", sprint_ids: Any = "",
                       last_n: Any = 0, state: str = "closed",
                       board_id: str = "", project: str = "") -> tuple[list[str], dict[str, Any] | None]:
    ids = _merge_id_fields(sprint_id, sprint_ids)
    n = _as_last_n(last_n)
    if n and not ids:
        listing = get_sprints(board_id, state or "closed", project)
        if listing.get("error"):
            return [], listing
        rows = listing.get("sprints") or []
        ids = [str(row["id"]) for row in rows if row.get("id")][-n:]
    if not ids:
        return [], {"error": "sprint_id, sprint_ids, or last_n is required"}
    return ids[:LAST_N_MAX], None


def _select_issue_ids(issue_id: str = "", issue_ids: Any = "") -> tuple[list[str], dict[str, Any] | None]:
    ids = _merge_id_fields(issue_id, issue_ids)
    if not ids:
        return [], {"error": "issue_id or issue_ids is required"}
    return ids[:LAST_N_MAX], None


def _page(rows: list[Any], start_at: int, max_results: int) -> dict[str, Any]:
    start = _start(start_at)
    cap = _cap(max_results)
    sliced = rows[start:start + cap]
    return {
        "items": sliced,
        "count": len(sliced),
        "total": len(rows),
        "start_at": start,
        "max_results": cap,
        "has_more": start + cap < len(rows),
    }


def compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Structured issue card for AI reasoning (lists + search)."""
    return {
        "id": issue["id"],
        "key": issue.get("key", issue["id"]),
        "summary": issue["summary"],
        "type": issue["type"],
        "issue_type": issue["type"],
        "status": issue["status"],
        "priority": issue.get("priority", "Medium"),
        "assignee": issue.get("assignee"),
        "assignee_name": _user_view(issue.get("assignee", "")).get("display_name"),
        "reporter": issue.get("reporter"),
        "story_points": issue.get("story_points", 0),
        "project": issue.get("project"),
        "sprint": issue.get("sprint"),
        "epic": issue.get("epic"),
        "parent": issue.get("parent"),
        "labels": list(issue.get("labels") or []),
        "components": list(issue.get("components") or []),
        "fix_versions": list(issue.get("fix_versions") or []),
        "due_date": issue.get("due_date"),
        "start_date": issue.get("start_date"),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "blocked_by": list(issue.get("blocked_by") or []),
        "blocks": list(issue.get("blocks") or []),
        "comments_count": len(issue.get("comments") or []),
        "is_done": _is_done(issue),
        "is_overdue": _is_overdue(issue),
        "days_since_update": _days_stale(issue),
    }


def full_issue(issue: dict[str, Any]) -> dict[str, Any]:
    card = compact_issue(issue)
    card["comments"] = list(issue.get("comments") or [])
    card["changelog"] = list(issue.get("changelog") or [])
    return card


def _find_issue(issue_id: str) -> dict[str, Any] | None:
    key = issue_id.strip().upper()
    return next((i for i in ISSUES if i["id"].upper() == key
                 or str(i.get("key", "")).upper() == key), None)


def _find_sprint(sprint_id: str) -> dict[str, Any] | None:
    key = (sprint_id or "").strip()
    if not key:
        return None
    if key in SPRINTS:
        return SPRINTS[key]
    upper = key.upper()
    if upper in SPRINTS:
        return SPRINTS[upper]
    lowered = key.lower()
    named = [s for s in SPRINTS.values() if str(s.get("name", "")).lower() == lowered]
    if len(named) == 1:
        return named[0]
    digits = re.sub(r"[^0-9]", "", key)
    if digits:
        matches = [s for sid, s in SPRINTS.items()
                   if sid.upper().endswith(f"-{digits}") or sid.upper() == f"SPR-{digits}"]
        active = [s for s in matches if s.get("state") == "active"]
        if len(active) == 1:
            return active[0]
        if len(matches) == 1:
            return matches[0]
    return named[0] if named else None


def _project_or_error(project: str) -> dict[str, Any] | None:
    key = project.strip().upper()
    if key in PROJECTS:
        return PROJECTS[key]
    lowered = project.strip().lower()
    for item in PROJECTS.values():
        if item["name"].lower() == lowered or item["key"].lower() == lowered:
            return item
    if len(lowered) >= 4:
        for item in PROJECTS.values():
            if lowered in item["name"].lower():
                return item
    return None


def _resolve_project(project: str) -> dict[str, Any]:
    """Project record, or ``{"error": ...}`` if missing/unknown."""
    _, err = _need(project, "project")
    if err:
        return err
    found = _project_or_error(project)
    return found if found is not None else {"error": f"unknown project '{project}'"}


def _project_key(project: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Optional project filter: (key, None), (None, None) for all, or error."""
    if not project:
        return None, None
    found = _project_or_error(project)
    if found is None:
        return None, {"error": f"unknown project '{project}'"}
    return found["key"], None


def _resolve_project_list(project: str | None = "", projects: Any = "",
                          *, required: bool = False
                          ) -> tuple[list[str], dict[str, Any] | None]:
    raw = parse_id_list(projects)
    single = (project or "").strip()
    if single and single not in raw:
        raw.insert(0, single)
    if not raw:
        if required:
            return [], {"error": "project or projects is required"}
        return [], None
    keys: list[str] = []
    missing: list[str] = []
    for item in raw:
        found = _project_or_error(item)
        if found is None:
            missing.append(item)
            continue
        if found["key"] not in keys:
            keys.append(found["key"])
    if not keys:
        return [], {"error": f"unknown project '{missing[0]}'"}
    return keys[:LAST_N_MAX], None


def _batch_items(rows: list[dict[str, Any]], *, key: str,
                 ids: list[str], missing: list[str]) -> dict[str, Any]:
    return {
        "items": rows,
        "count": len(rows),
        "missing": missing,
        key: ids,
    }


def _issues_across_projects(keys: list[str], fetch_one) -> dict[str, Any]:
    by_project: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    for key in keys:
        row = fetch_one(key)
        issues = row.get("issues") or []
        by_project.append({
            "project": key,
            "count": row.get("count", len(issues)),
            "issues": issues[:8],
        })
        room = max(0, TOTAL_ISSUE_CAP - len(flat))
        flat.extend(issues[:room])
    return {
        "by_project": by_project,
        "issues": flat,
        "count": sum(int(row["count"]) for row in by_project),
        "projects": keys,
        "totals": {
            "projects": len(by_project),
            "issues": sum(int(row["count"]) for row in by_project),
        },
    }


def _active_sprint_for_project(project_key: str) -> dict[str, Any] | None:
    project = PROJECTS.get(project_key)
    if not project:
        return None
    board_id = project["board_id"]
    return next((s for s in SPRINTS.values()
                 if s["board_id"] == board_id and s["state"] == "active"), None)


def _issues_for(project: str | None = None, sprint_id: str | None = None) -> list[dict]:
    rows = list(ISSUES)
    if project:
        key = project.upper()
        rows = [i for i in rows if i["project"] == key]
    if sprint_id:
        rows = [i for i in rows if i.get("sprint") == sprint_id]
    return rows




def get_projects() -> dict[str, Any]:
    items = []
    for project in PROJECTS.values():
        issues = _issues_for(project["key"])
        open_issues = [i for i in issues if not _is_done(i)]
        items.append({
            **{k: v for k, v in project.items() if k != "members"},
            "lead_name": _user_view(project["lead"])["display_name"],
            "member_count": len(project.get("members") or []),
            "open_issue_count": len(open_issues),
            "issue_count": len(issues),
        })
    return {"projects": items, "count": len(items)}


def get_project(project: str = "", projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects, required=True)
    if err:
        return err
    if len(keys) == 1 and not parse_id_list(projects):
        return _one_project(keys[0])
    rows = [_one_project(key) for key in keys]
    return _batch_items(rows, key="projects", ids=keys, missing=[])


def _one_project(key: str) -> dict[str, Any]:
    found = _resolve_project(key)
    if "error" in found:
        return found
    issues = _issues_for(found["key"])
    sprint = _active_sprint_for_project(found["key"])
    versions = [dict(v) for v in VERSIONS if v["project"] == found["key"]]
    return {
        "project": {
            **found,
            "lead_name": _user_view(found["lead"])["display_name"],
            "members": [_user_view(m) for m in found.get("members", [])],
        },
        "active_sprint": dict(sprint) if sprint else None,
        "releases": versions,
        "issue_counts": {
            "total": len(issues),
            "open": len([i for i in issues if not _is_done(i)]),
            "done": len([i for i in issues if _is_done(i)]),
            "bugs_open": len([i for i in issues if i["type"] == "Bug" and not _is_done(i)]),
        },
    }


def get_board(board_id: str = "", board_ids: str = "") -> dict[str, Any]:
    ids = _merge_id_fields(board_id, board_ids)
    if not ids:
        return {"error": "board_id or board_ids is required"}
    if len(ids) == 1 and not parse_id_list(board_ids):
        board = BOARDS.get(ids[0])
        if board is None:
            return {"error": f"unknown board '{ids[0]}'"}
        return {"board": {**board, "id": ids[0]}}
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for bid in ids:
        board = BOARDS.get(bid) or BOARDS.get(bid.upper())
        if board is None:
            missing.append(bid)
            continue
        rows.append({**board, "id": bid})
    return {"boards": rows, "count": len(rows), "missing": missing, "board_ids": [r["id"] for r in rows]}


def get_sprints(board_id: str = "", state: str = "closed",
                project: str = "", last_n: int = 0,
                include_metrics: bool = False, board_ids: str = "",
                projects: str = "") -> dict[str, Any]:
    wanted = (state or "closed").strip().lower()
    if wanted not in {"closed", "active", "future"}:
        return {"error": f"invalid sprint state '{state}'"}
    allowed: set[str] | None = None
    board_list = _merge_id_fields(board_id, board_ids)
    if board_list:
        allowed = set()
        for bid in board_list:
            if bid not in BOARDS:
                return {"error": f"unknown board '{bid}'"}
            allowed.add(bid)
    else:
        keys, err = _resolve_project_list(project, projects)
        if err:
            return err
        if keys:
            allowed = {bid for bid, board in BOARDS.items()
                       if board.get("project") in keys}
    sprints = [dict(s) for s in SPRINTS.values()
               if s["state"] == wanted and (allowed is None or s["board_id"] in allowed)]
    sprints.sort(key=lambda s: s.get("start") or "")
    n = _as_last_n(last_n)
    if n:
        sprints = sprints[-n:]
    if include_metrics in (True, "true", "True", "1", "yes"):
        enriched = []
        for sprint in sprints:
            row = dict(sprint)
            row.update(_sprint_metrics(str(sprint.get("id") or "")))
            enriched.append(row)
        sprints = enriched
    return {
        "sprints": sprints,
        "count": len(sprints),
        "last_n": n or None,
        "include_metrics": bool(include_metrics),
    }


def get_sprint(sprint_id: str = "", sprint_ids: str = "",
               last_n: int = 0) -> dict[str, Any]:
    ids, err = _select_sprint_ids(
        sprint_id=sprint_id, sprint_ids=sprint_ids, last_n=last_n)
    if err:
        return err
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for sid in ids:
        sprint = _find_sprint(sid)
        if sprint is None:
            missing.append(sid)
            continue
        metrics = _sprint_metrics(sid)
        rows.append({"sprint": dict(sprint), **metrics})
    if len(ids) == 1 and not parse_id_list(sprint_ids) and not _as_last_n(last_n):
        if missing:
            return {"error": f"unknown sprint '{ids[0]}'"}
        return rows[0] if rows else {"error": f"unknown sprint '{ids[0]}'"}
    return {"sprints": rows, "count": len(rows), "missing": missing,
            "sprint_ids": [row["sprint"]["id"] for row in rows if row.get("sprint")]}


def get_sprint_issues(sprint_id: str = "", max_results: int = 50,
                      start_at: int = 0, sprint_ids: str = "",
                      last_n: int = 0, issue_type: str = "",
                      state: str = "closed") -> dict[str, Any]:
    ids, err = _select_sprint_ids(
        sprint_id=sprint_id, sprint_ids=sprint_ids, last_n=last_n,
        state=state)
    if err:
        return err
    wanted_type = (issue_type or "").strip()
    batch = len(ids) > 1 or bool(parse_id_list(sprint_ids)) or _as_last_n(last_n) > 0
    if not batch:
        checked = ids[0]
        if checked not in SPRINTS and checked != "backlog":
            return {"error": f"unknown sprint '{sprint_id or checked}'"}
        issues = [compact_issue(i) for i in ISSUES if i.get("sprint") == checked]
        if wanted_type:
            issues = [i for i in issues if i.get("type", "").lower() == wanted_type.lower()]
        page = _page(issues, start_at, max_results)
        return {
            "issues": page["items"],
            "count": page["count"],
            "total_matched": page["total"],
            "sprint_id": checked,
            "start_at": page["start_at"],
            "has_more": page["has_more"],
            "type_counts": _sprint_metrics(checked)["type_counts"],
            "bug_count": _sprint_metrics(checked)["bug_count"],
        }
    by_sprint: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    missing: list[str] = []
    totals_by_type: dict[str, int] = {}
    for sid in ids:
        if sid not in SPRINTS and sid != "backlog":
            missing.append(sid)
            continue
        issues = [compact_issue(i) for i in ISSUES if i.get("sprint") == sid]
        if wanted_type:
            issues = [i for i in issues
                      if i.get("type", "").lower() == wanted_type.lower()]
        metrics = _sprint_metrics(sid)
        sprint = SPRINTS.get(sid) or {"id": sid}
        sample = issues[:PER_SPRINT_ISSUE_CAP]
        by_sprint.append({
            "sprint_id": sid,
            "name": sprint.get("name", sid),
            "state": sprint.get("state"),
            "issue_count": len(issues),
            "bug_count": metrics["bug_count"] if not wanted_type else sum(
                1 for i in issues if i.get("type") == "Bug"),
            "type_counts": metrics["type_counts"] if not wanted_type else {
                wanted_type: len(issues)},
            "completed_story_points": metrics["completed_story_points"],
            "remaining_story_points": metrics["remaining_story_points"],
            "issues": sample,
        })
        for kind, count in (by_sprint[-1]["type_counts"] or {}).items():
            totals_by_type[str(kind)] = totals_by_type.get(str(kind), 0) + int(count)
        room = max(0, TOTAL_ISSUE_CAP - len(flat))
        flat.extend(sample[:room])
    return {
        "sprint_ids": [row["sprint_id"] for row in by_sprint],
        "by_sprint": by_sprint,
        "issues": flat,
        "count": len(flat),
        "totals": {
            "sprints": len(by_sprint),
            "issues": sum(row["issue_count"] for row in by_sprint),
            "bugs": sum(row["bug_count"] for row in by_sprint),
            "type_counts": totals_by_type,
        },
        "missing": missing,
        "last_n": _as_last_n(last_n) or None,
        "issue_type": wanted_type or None,
    }


def get_issue(issue_id: str = "", issue_ids: str = "") -> dict[str, Any]:
    ids, err = _select_issue_ids(issue_id, issue_ids)
    if err:
        return err
    if len(ids) == 1 and not parse_id_list(issue_ids):
        found = _find_issue(ids[0])
        if found is None:
            return {"error": f"issue '{ids[0]}' not found"}
        return {"issue": full_issue(found)}
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in ids:
        found = _find_issue(key)
        if found is None:
            missing.append(key)
            continue
        rows.append(compact_issue(found))
    return {"issues": rows, "count": len(rows), "missing": missing,
            "issue_ids": [row["id"] for row in rows]}


def get_worklogs(issue_id: str = "", issue_ids: str = "") -> dict[str, Any]:
    ids, err = _select_issue_ids(issue_id, issue_ids)
    if err:
        return err
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in ids:
        found = _find_issue(key)
        if found is None:
            missing.append(key)
            continue
        entries = list(WORKLOGS.get(found["id"], []))
        items.append({
            "issue_id": found["id"],
            "worklogs": entries,
            "total_hours": sum(float(w.get("hours") or 0) for w in entries),
            "count": len(entries),
        })
    if len(ids) == 1 and not parse_id_list(issue_ids):
        if missing:
            return {"error": f"issue '{ids[0]}' not found"}
        row = items[0]
        return {"worklogs": row["worklogs"], "issue_id": row["issue_id"],
                "total_hours": row["total_hours"]}
    return {"items": items, "count": len(items), "missing": missing,
            "total_hours": sum(float(row["total_hours"]) for row in items)}


def get_issue_comments(issue_id: str = "", issue_ids: str = "") -> dict[str, Any]:
    ids, err = _select_issue_ids(issue_id, issue_ids)
    if err:
        return err
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in ids:
        found = _find_issue(key)
        if found is None:
            missing.append(key)
            continue
        comments = list(found.get("comments") or [])
        items.append({"issue_id": found["id"], "comments": comments,
                      "count": len(comments)})
    if len(ids) == 1 and not parse_id_list(issue_ids):
        if missing:
            return {"error": f"issue '{ids[0]}' not found"}
        row = items[0]
        return {"issue_id": row["issue_id"], "comments": row["comments"],
                "count": row["count"]}
    return {"items": items, "count": len(items), "missing": missing}


def get_issue_history(issue_id: str = "", issue_ids: str = "") -> dict[str, Any]:
    ids, err = _select_issue_ids(issue_id, issue_ids)
    if err:
        return err
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in ids:
        found = _find_issue(key)
        if found is None:
            missing.append(key)
            continue
        log = list(found.get("changelog") or [])
        items.append({"issue_id": found["id"], "changelog": log, "count": len(log)})
    if len(ids) == 1 and not parse_id_list(issue_ids):
        if missing:
            return {"error": f"issue '{ids[0]}' not found"}
        row = items[0]
        return {"issue_id": row["issue_id"], "changelog": row["changelog"],
                "count": row["count"]}
    return {"items": items, "count": len(items), "missing": missing}


def get_users(project: str | None = None, projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = []
        for key in keys:
            row = get_users(key)
            if not row.get("error"):
                rows.append(row)
        return _batch_items(rows, key="projects", ids=keys, missing=[])
    if project:
        found = _resolve_project(project)
        if "error" in found:
            return found
        members = [_user_view(m) for m in found.get("members", [])]
        return {"users": members, "count": len(members), "project": found["key"]}
    people = [_user_view(k) for k in USERS if k != "unassigned"]
    return {"users": people, "count": len(people)}


def get_team_members(project: str = "", projects: str = "") -> dict[str, Any]:
    return get_users(project or None, projects)


def get_team_workload(project: str | None = None,
                      sprint_id: str | None = None,
                      projects: str = "", sprint_ids: str = "",
                      last_n: int = 0) -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = [get_team_workload(key, sprint_id) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=[])
    if parse_id_list(sprint_ids) or _as_last_n(last_n):
        sid_list, serr = _select_sprint_ids(
            sprint_id=sprint_id or "", sprint_ids=sprint_ids, last_n=last_n)
        if serr:
            return serr
        rows = [get_team_workload(project, sid) for sid in sid_list]
        ok = [row for row in rows if not row.get("error")]
        missing = [sid for sid, row in zip(sid_list, rows) if row.get("error")]
        return _batch_items(ok, key="sprint_ids", ids=sid_list, missing=missing)
    resolved = None
    if project:
        resolved = _resolve_project(project)
        if "error" in resolved:
            return resolved
    if sprint_id and sprint_id not in SPRINTS and sprint_id != "backlog":
        return {"error": f"unknown sprint '{sprint_id}'"}
    rows = _issues_for(resolved["key"] if resolved else None,
                       sprint_id.strip() if sprint_id else None)
    open_issues = [i for i in rows if not _is_done(i)]
    by_assignee: dict[str, dict[str, Any]] = {}
    for issue in open_issues:
        owner = issue.get("assignee") or "unassigned"
        bucket = by_assignee.setdefault(owner, {
            "assignee": owner,
            "assignee_name": _user_view(owner)["display_name"],
            "open_issues": 0,
            "remaining_points": 0,
            "blocked": 0,
            "overdue": 0,
            "in_progress": 0,
            "issue_keys": [],
        })
        bucket["open_issues"] += 1
        bucket["remaining_points"] += int(issue.get("story_points") or 0)
        bucket["issue_keys"].append(issue["id"])
        if issue.get("blocked_by"):
            bucket["blocked"] += 1
        if _is_overdue(issue):
            bucket["overdue"] += 1
        if issue.get("status") == "In Progress":
            bucket["in_progress"] += 1
    workload = sorted(by_assignee.values(),
                      key=lambda r: (-r["remaining_points"], -r["open_issues"]))
    idle = []
    if resolved:
        assigned = {row["assignee"] for row in workload}
        idle = [_user_view(m) for m in resolved.get("members", [])
                if m not in assigned
                and "Engineer" in (USERS.get(m, {}).get("role") or "")]
    return {
        "workload": workload,
        "count": len(workload),
        "idle_engineers": idle,
        "project": resolved["key"] if resolved else None,
        "sprint_id": sprint_id,
    }


def get_releases(project: str = "", projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = [get_releases(key) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=[])
    if not keys:
        versions = [dict(v) for v in VERSIONS]
        return {"releases": versions, "count": len(versions)}
    found = _resolve_project(keys[0])
    if "error" in found:
        return found
    versions = [dict(v) for v in VERSIONS if v["project"] == found["key"]]
    return {"project": found["key"], "releases": versions, "count": len(versions)}


def _match_version(project_key: str | None, version: str) -> dict[str, Any] | None:
    ver = version.strip().lower()
    candidates = [v for v in VERSIONS
                  if project_key is None or v["project"] == project_key]
    exact = next((v for v in candidates if v["name"].lower() == ver), None)
    if exact:
        return exact
    return next((v for v in candidates
                 if v["name"].lower().startswith(ver) or ver in v["name"].lower()), None)


def get_release_status(project: str = "", version: str = "",
                       versions: str = "", projects: str = "") -> dict[str, Any]:
    ver_list = parse_id_list(versions)
    single = (version or "").strip()
    if single and single not in ver_list:
        ver_list.insert(0, single)
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if len(ver_list) > 1 or parse_id_list(versions):
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        scope = keys[0] if len(keys) == 1 else ""
        for ver in ver_list:
            row = get_release_status(scope, ver)
            if row.get("error"):
                missing.append(ver)
                continue
            rows.append(row)
        return {
            **_batch_items(rows, key="versions", ids=ver_list, missing=missing),
            "totals": {
                "versions": len(rows),
                "open": sum(int((row.get("issue_counts") or {}).get("open") or 0)
                            for row in rows),
                "blocked": sum(int((row.get("issue_counts") or {}).get("blocked") or 0)
                               for row in rows),
            },
        }
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = [get_release_status(key, single) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        missing = [key for key, row in zip(keys, rows) if row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=missing)
    ver, err = _need(version, "version")
    if err:
        return err
    found: dict[str, Any] | None = None
    if (project or "").strip():
        found = _resolve_project(project)
        if "error" in found:
            return found
    match = _match_version(found["key"] if found else None, ver)
    if match is None:
        return {"error": f"unknown version '{version}'"}
    scoped = [i for i in _issues_for(match["project"])
              if ver in (i.get("fix_versions") or [])
              or match["name"] in (i.get("fix_versions") or [])]
    open_issues = [compact_issue(i) for i in scoped if not _is_done(i)]
    done_issues = [compact_issue(i) for i in scoped if _is_done(i)]
    bugs = [i for i in open_issues if i["type"] == "Bug"]
    blocked = [i for i in open_issues if i["blocked_by"]]
    overdue = [i for i in open_issues if i["is_overdue"]]
    total_points = sum(int(i.get("story_points") or 0) for i in scoped)
    done_points = sum(int(i.get("story_points") or 0) for i in scoped if _is_done(i))
    pct = round(100 * done_points / total_points, 1) if total_points else (
        100.0 if scoped and all(_is_done(i) for i in scoped) else 0.0)
    return {
        "release": dict(match),
        "project": match["project"],
        "completion_percent": pct,
        "issue_counts": {
            "total": len(scoped), "done": len(done_issues), "open": len(open_issues),
            "open_bugs": len(bugs), "blocked": len(blocked), "overdue": len(overdue),
        },
        "open_issues": open_issues,
        "blocking_issues": blocked,
        "unresolved_bugs": bugs,
    }


def get_blocked_issues(project: str | None = None,
                       projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        return _issues_across_projects(keys, lambda key: get_blocked_issues(key))
    key, err = _project_key(project)
    if err:
        return err
    rows = []
    for issue in _issues_for(key):
        if _is_done(issue):
            continue
        blockers = list(issue.get("blocked_by") or [])
        if not blockers:
            continue
        unresolved = [b for b in blockers
                      if (found := _find_issue(b)) is None or not _is_done(found)]
        if not unresolved:
            continue
        card = compact_issue(issue)
        card["unresolved_blockers"] = unresolved
        rows.append(card)
    return {"issues": rows, "count": len(rows), "project": key}


def get_overdue_issues(project: str | None = None,
                       projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        return _issues_across_projects(keys, lambda key: get_overdue_issues(key))
    key, err = _project_key(project)
    if err:
        return err
    rows = [compact_issue(i) for i in _issues_for(key) if _is_overdue(i)]
    rows.sort(key=lambda i: i.get("due_date") or "")
    return {"issues": rows, "count": len(rows), "as_of": _as_of().isoformat(),
            "project": key}


def get_stuck_issues(project: str | None = None, days: int = 7,
                     projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        return _issues_across_projects(
            keys, lambda key: get_stuck_issues(key, days))
    key, err = _project_key(project)
    if err:
        return err
    try:
        threshold = max(1, int(days))
    except (TypeError, ValueError):
        threshold = 7
    rows = []
    for issue in _issues_for(key):
        if issue.get("status") != "In Progress":
            continue
        stale = _days_stale(issue)
        if stale >= threshold:
            card = compact_issue(issue)
            card["days_in_progress_estimate"] = stale
            rows.append(card)
    rows.sort(key=lambda i: -i.get("days_since_update", 0))
    return {"issues": rows, "count": len(rows), "days": threshold, "project": key}


def get_dependencies(issue_id: str | None = None,
                     project: str | None = None,
                     issue_ids: str = "", projects: str = "") -> dict[str, Any]:
    if issue_id or parse_id_list(issue_ids):
        ids, err = _select_issue_ids(issue_id or "", issue_ids)
        if err:
            return err
        if len(ids) > 1 or parse_id_list(issue_ids):
            rows = [get_dependencies(iid) for iid in ids]
            ok = [row for row in rows if not row.get("error")]
            missing = [iid for iid, row in zip(ids, rows) if row.get("error")]
            return _batch_items(ok, key="issue_ids", ids=ids, missing=missing)
    if issue_id:
        found = _find_issue(issue_id)
        if found is None:
            return {"error": f"issue '{issue_id}' not found"}
        return {
            "issue_id": found["id"],
            "blocked_by": [compact_issue(b) for bid in found.get("blocked_by") or []
                           if (b := _find_issue(bid))],
            "blocks": [compact_issue(b) for bid in found.get("blocks") or []
                       if (b := _find_issue(bid))],
            "missing_links": [bid for bid in
                              list(found.get("blocked_by") or []) + list(found.get("blocks") or [])
                              if _find_issue(bid) is None],
        }
    keys, perr = _resolve_project_list(project, projects)
    if perr:
        return perr
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = [get_dependencies(None, key) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        missing = [key for key, row in zip(keys, rows) if row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=missing)
    key, err = _project_key(project)
    if err:
        return err
    links = []
    for issue in _issues_for(key):
        for blocker in issue.get("blocked_by") or []:
            links.append({
                "type": "blocks",
                "from": blocker,
                "to": issue["id"],
                "from_done": bool((src := _find_issue(blocker)) and _is_done(src)),
                "to_status": issue["status"],
                "to_summary": issue["summary"],
            })
    return {"links": links, "count": len(links), "project": key}


def get_epics(project: str = "", projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects, required=True)
    if err:
        return err
    if len(keys) > 1 or parse_id_list(projects):
        rows = [get_epics(key) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=[])
    found = _resolve_project(keys[0] if keys else project)
    if "error" in found:
        return found
    epics = []
    for epic in _issues_for(found["key"]):
        if epic["type"] != "Epic":
            continue
        children = [i for i in _issues_for(found["key"]) if i.get("epic") == epic["id"]]
        done = [i for i in children if _is_done(i)]
        epics.append({
            **compact_issue(epic),
            "child_count": len(children),
            "child_done": len(done),
            "children": [compact_issue(c) for c in children],
        })
    return {"epics": epics, "count": len(epics), "project": found["key"]}


def get_backlog(project: str = "", max_results: int = 20, start_at: int = 0,
                projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects, required=True)
    if err:
        return err
    if len(keys) > 1 or parse_id_list(projects):
        return _issues_across_projects(
            keys, lambda key: get_backlog(key, max_results, start_at))
    found = _resolve_project(keys[0] if keys else project)
    if "error" in found:
        return found
    rows = [compact_issue(i) for i in _issues_for(found["key"])
            if i.get("sprint") in {None, "", "backlog"} and i["type"] != "Epic"]
    page = _page(rows, start_at, max_results)
    return {"issues": page["items"], "count": page["count"], "total": page["total"],
            "project": found["key"], "has_more": page["has_more"]}


def get_recent_activity(project: str | None = None, days: int = 7,
                        max_results: int = 20, projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        rows = [get_recent_activity(key, days, max_results) for key in keys]
        ok = [row for row in rows if not row.get("error")]
        return _batch_items(ok, key="projects", ids=keys, missing=[])
    key, err = _project_key(project)
    if err:
        return err
    try:
        window = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        window = 7
    since = _as_of() - timedelta(days=window)
    events: list[dict[str, Any]] = []
    for issue in _issues_for(key):
        for change in issue.get("changelog") or []:
            when = _parse_day(change.get("at"))
            if when and when >= since:
                events.append({
                    "kind": "changelog", "issue_id": issue["id"],
                    "summary": issue["summary"], **change,
                })
        for comment in issue.get("comments") or []:
            when = _parse_day(comment.get("date") or comment.get("created"))
            if when and when >= since:
                events.append({
                    "kind": "comment", "issue_id": issue["id"],
                    "summary": issue["summary"],
                    "author": comment.get("author"),
                    "body": comment.get("body"),
                    "at": comment.get("date") or comment.get("created"),
                })
    events.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    cap = _cap(max_results)
    return {"activity": events[:cap], "count": len(events[:cap]),
            "total": len(events), "since": since.isoformat(), "project": key}


def _one_sprint_report(sprint_id: str, *, compact: bool = False) -> dict[str, Any]:
    sprint = _find_sprint(sprint_id)
    if sprint is None:
        return {"error": f"unknown sprint '{sprint_id}'"}
    issues = [i for i in ISSUES if i.get("sprint") == sprint_id]
    cards = [compact_issue(i) for i in issues]
    start = _parse_day(sprint.get("start"))
    added_after_start = [
        compact_issue(i) for i in issues
        if start and (created := _parse_day(i.get("created"))) and created > start
    ]
    remaining = [c for c in cards if not c["is_done"]]
    blocked = [c for c in remaining if c["blocked_by"]]
    overdue = [c for c in remaining if c["is_overdue"]]
    remaining_points = sum(c["story_points"] for c in remaining)
    end = _parse_day(sprint.get("end"))
    days_left = (end - _as_of()).days if end else None
    at_risk = [c for c in remaining if c["blocked_by"] or c["is_overdue"]
               or c["priority"] in {"Highest", "Critical"}]
    metrics = _sprint_metrics(sprint_id)
    report = {
        "sprint": dict(sprint),
        "sprint_id": sprint.get("id", sprint_id),
        "as_of": _as_of().isoformat(),
        "days_remaining": days_left,
        "committed_points": sprint.get("committed_points"),
        "completed_points_recorded": sprint.get("completed_points"),
        "issue_points_done": sum(c["story_points"] for c in cards if c["is_done"]),
        "issue_points_remaining": remaining_points,
        "status_counts": _status_counts(cards),
        "bug_count": metrics["bug_count"],
        "type_counts": metrics["type_counts"],
        "assessment_inputs": {
            "remaining_issue_count": len(remaining),
            "blocked_count": len(blocked),
            "overdue_count": len(overdue),
            "added_after_start_count": len(added_after_start),
        },
    }
    if compact:
        return report
    report.update({
        "added_after_start": added_after_start,
        "blocked": blocked,
        "overdue": overdue,
        "at_risk": at_risk,
        "remaining": remaining,
    })
    return report


def get_sprint_report(sprint_id: str = "", sprint_ids: str = "",
                      last_n: int = 0) -> dict[str, Any]:
    ids, err = _select_sprint_ids(
        sprint_id=sprint_id, sprint_ids=sprint_ids, last_n=last_n)
    if err:
        return err
    batch = len(ids) > 1 or bool(parse_id_list(sprint_ids)) or _as_last_n(last_n) > 0
    if not batch:
        return _one_sprint_report(ids[0], compact=False)
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    for sid in ids:
        row = _one_sprint_report(sid, compact=True)
        if row.get("error"):
            missing.append(sid)
            continue
        reports.append(row)
    return {
        "reports": reports,
        "count": len(reports),
        "missing": missing,
        "sprint_ids": [row.get("sprint_id") for row in reports],
        "last_n": _as_last_n(last_n) or None,
        "comparison": {
            "sprint_ids": [row.get("sprint_id") for row in reports],
            "names": [row.get("sprint", {}).get("name") for row in reports],
            "bug_counts": [row.get("bug_count", 0) for row in reports],
            "issue_counts": [sum((row.get("type_counts") or {}).values())
                             for row in reports],
            "completed_points": [row.get("completed_points_recorded")
                                 or row.get("issue_points_done") for row in reports],
            "remaining_points": [row.get("issue_points_remaining") for row in reports],
        },
    }


def get_project_health(project: str = "", projects: str = "") -> dict[str, Any]:
    keys, err = _resolve_project_list(project, projects)
    if err:
        return err
    if not keys and not (project or "").strip() and not parse_id_list(projects):
        items = [get_project_health(key) for key in PROJECTS]
        items = [item for item in items if "error" not in item]
        labels = [str(item.get("overall") or "") for item in items]
        overall = "At Risk" if "At Risk" in labels else (labels[0] if labels else "Unknown")
        return {"projects": items, "count": len(items), "overall": overall,
                "totals": {"projects": len(items),
                           "at_risk": labels.count("At Risk")}}
    if keys and (len(keys) > 1 or parse_id_list(projects)):
        items = [get_project_health(key) for key in keys]
        ok = [item for item in items if "error" not in item]
        labels = [str(item.get("overall") or "") for item in ok]
        overall = "At Risk" if "At Risk" in labels else (labels[0] if labels else "Unknown")
        return {
            "projects": ok,
            "count": len(ok),
            "overall": overall,
            "missing": [key for key, item in zip(keys, items) if "error" in item],
            "totals": {"projects": len(ok), "at_risk": labels.count("At Risk")},
        }
    found = _resolve_project(project)
    if "error" in found:
        return found
    key = found["key"]
    issues = _issues_for(key)
    open_issues = [i for i in issues if not _is_done(i)]
    blocked = get_blocked_issues(key)["issues"]
    overdue = get_overdue_issues(key)["issues"]
    bugs = [i for i in open_issues if i["type"] == "Bug"]
    critical_bugs = [i for i in bugs
                     if i.get("priority") in {"Highest", "Critical", "High"}]
    sprint = _active_sprint_for_project(key)
    report = get_sprint_report(sprint["id"]) if sprint else None
    versions = [v for v in VERSIONS if v["project"] == key and not v["released"]]
    release = None
    if versions:
        versions_sorted = sorted(versions, key=lambda v: v.get("release_date") or "9999")
        release = get_release_status(key, versions_sorted[0]["name"])

    board = found["board_id"]
    closed = [s for s in SPRINTS.values()
              if s["board_id"] == board and s["state"] == "closed"]
    closed.sort(key=lambda s: s.get("end") or "")
    velocities = [int(s.get("completed_points") or 0) for s in closed[-3:]]
    avg_velocity = round(sum(velocities) / len(velocities), 1) if velocities else None
    remaining_points = (report or {}).get("issue_points_remaining")
    days_left = (report or {}).get("days_remaining")

    workload = get_team_workload(key)["workload"]
    schedule = _grade(
        at_risk=bool(
            (remaining_points and avg_velocity and remaining_points > avg_velocity)
            or (days_left is not None and days_left <= 3 and remaining_points
                and remaining_points > 8)
            or len(overdue) >= 2
        ),
        elevated=bool(overdue or (days_left is not None and days_left <= 7
                                  and remaining_points and remaining_points > 5)),
    )
    scope = _grade(
        at_risk=bool(report and report["assessment_inputs"]["added_after_start_count"] >= 3),
        elevated=bool(report and report["assessment_inputs"]["added_after_start_count"] >= 1),
    )
    engineering = _grade(
        at_risk=any(row["open_issues"] >= 4 for row in workload),
        elevated=any(row["blocked"] >= 1 and row["open_issues"] >= 2
                     for row in workload),
    )
    bug_dim = _grade(
        at_risk=any(i.get("priority") in {"Highest", "Critical"} for i in bugs),
        elevated=len(critical_bugs) >= 1,
    )
    deps = _grade(at_risk=len(blocked) >= 2, elevated=len(blocked) >= 1)
    ranks = {"Healthy": 0, "Elevated": 1, "At Risk": 2}
    overall = max(
        (schedule, scope, engineering, bug_dim, deps),
        key=lambda label: ranks[label],
    )
    return {
        "project": key,
        "project_name": found["name"],
        "as_of": _as_of().isoformat(),
        "assessment": "derived_from_project_data",
        "overall": overall,
        "schedule": schedule,
        "scope": scope,
        "engineering": engineering,
        "bugs": bug_dim,
        "dependencies": deps,
        "dimensions": {
            "schedule": schedule,
            "scope": scope,
            "engineering": engineering,
            "bugs": bug_dim,
            "dependencies": deps,
            "overall": overall,
        },
        "evidence": {
            "open_issues": len(open_issues),
            "blocked_issues": len(blocked),
            "overdue_issues": len(overdue),
            "open_bugs": len(bugs),
            "critical_or_high_bugs": len(critical_bugs),
            "active_sprint": sprint["id"] if sprint else None,
            "remaining_story_points": remaining_points,
            "days_remaining_in_sprint": days_left,
            "recent_velocity": velocities,
            "average_recent_velocity": avg_velocity,
            "unreleased_versions": [v["name"] for v in versions],
            "nearest_release": None if release is None or "error" in release
            else {
                "name": release["release"]["name"],
                "completion_percent": release["completion_percent"],
                "open": release["issue_counts"]["open"],
                "blocked": release["issue_counts"]["blocked"],
            },
        },
    }


def search_issues(jql: str, max_results: int = 20, start_at: int = 0) -> dict[str, Any]:
    if not (jql or "").strip():
        return {"error": "jql is required"}
    try:
        filters = parse_jql(jql)
    except ValueError as exc:
        return {"error": str(exc)}
    rows = [i for i in ISSUES if _matches(i, filters)]
    cards = [compact_issue(i) for i in rows]
    page = _page(cards, start_at, max_results)
    return {
        "issues": page["items"],
        "count": page["count"],
        "total_matched": page["total"],
        "start_at": page["start_at"],
        "has_more": page["has_more"],
    }


def parse_jql(jql: str) -> dict[str, str]:
    """Validated JQL subset: field = value joined by AND (server-side caps)."""
    filters: dict[str, str] = {}
    text = " ".join(jql.split()).lower()
    for clause in text.split(" and "):
        clause = clause.strip()
        if not clause or clause.startswith("order by"):
            continue
        if "=" not in clause:
            raise ValueError(
                f"unsupported JQL clause (only field = value allowed): '{clause}'")
        field_name, _, value = clause.partition("=")
        field_name = field_name.strip()
        if field_name not in JQL_FIELDS:
            raise ValueError(f"unsupported JQL field '{field_name}'")
        filters[field_name] = value.strip().strip("'\"")
    return filters


def _resolve_assignee(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return raw
    if raw in USERS:
        return raw
    for account, profile in USERS.items():
        name = str(profile.get("display_name") or "").lower()
        if raw == name or raw in name.split() or name.startswith(raw):
            return account
    return raw


def _matches(issue: dict[str, Any], filters: dict[str, str]) -> bool:
    for field_name, expected in filters.items():
        if field_name in {"labels"}:
            hay = [str(x).lower() for x in (issue.get("labels") or [])]
            if expected not in hay:
                return False
            continue
        if field_name == "component":
            hay = [str(x).lower() for x in (issue.get("components") or [])]
            if expected not in hay:
                return False
            continue
        if field_name in {"fixversion", "version"}:
            hay = [str(x).lower() for x in (issue.get("fix_versions") or [])]
            if expected not in hay:
                return False
            continue
        if field_name in {"summary", "text"}:
            if expected not in str(issue.get("summary") or "").lower():
                return False
            continue
        if field_name == "status" and expected in {"open", "unresolved"}:
            if _is_done(issue):
                return False
            continue
        if field_name == "assignee":
            actual = str(issue.get("assignee") or "").lower()
            display = str(_user_view(issue.get("assignee", "")).get("display_name") or "").lower()
            resolved = _resolve_assignee(expected)
            if expected not in {actual, display} and resolved != actual:
                return False
            continue
        actual = issue.get("type" if field_name == "type" else field_name, "")
        if str(actual).lower() != expected:
            return False
    return True




def _next_key(project: str) -> str:
    nums = []
    prefix = f"{project}-"
    for issue in ISSUES:
        if issue["id"].startswith(prefix):
            tail = issue["id"][len(prefix):]
            if tail.isdigit():
                nums.append(int(tail))
    return f"{project}-{max(nums, default=0) + 1}"


def create_issue(project: str, issue_type: str, summary: str) -> dict[str, Any]:
    found = _resolve_project(project)
    if "error" in found:
        return found
    if issue_type not in CREATE_TYPES:
        return {"error": f"invalid issue type '{issue_type}'"}
    summary_text = (summary or "").strip()
    if not summary_text:
        return {"error": "summary is required"}
    today = _as_of().isoformat()
    with _LOCK:
        issue = {
            "id": _next_key(found["key"]),
            "key": "",
            "summary": summary_text,
            "type": issue_type,
            "status": "To Do",
            "story_points": 0,
            "assignee": "unassigned",
            "reporter": "platform-agent",
            "priority": "Medium",
            "project": found["key"],
            "sprint": "backlog",
            "epic": None,
            "parent": None,
            "labels": [],
            "components": [],
            "fix_versions": [],
            "due_date": None,
            "start_date": None,
            "created": today,
            "updated": today,
            "blocked_by": [],
            "blocks": [],
            "comments": [],
            "changelog": [],
        }
        issue["key"] = issue["id"]
        ISSUES.append(issue)
        snapshot = compact_issue(issue)
    return {"issue": snapshot, "created": True}


def update_issue(issue_id: str, summary: str) -> dict[str, Any]:
    checked, err = _need(issue_id, "issue_id")
    if err:
        return err
    summary_text = (summary or "").strip()
    if not summary_text:
        return {"error": "summary is required"}
    with _LOCK:
        issue = _find_issue(checked)
        if issue is None:
            return {"error": f"issue '{issue_id}' not found"}
        previous = issue["summary"]
        issue["summary"] = summary_text
        issue["updated"] = _as_of().isoformat()
        issue.setdefault("changelog", []).append(
            _change_now("platform-agent", "summary", previous, summary_text))
        snapshot = compact_issue(issue)
    return {"issue": snapshot, "updated": True}


def transition_issue(issue_id: str, status: str) -> dict[str, Any]:
    checked, err = _need(issue_id, "issue_id")
    if err:
        return err
    status_text = (status or "").strip()
    if not status_text:
        return {"error": "status is required"}
    with _LOCK:
        issue = _find_issue(checked)
        if issue is None:
            return {"error": f"issue '{issue_id}' not found"}
        legal = LEGAL_TRANSITIONS.get(issue["status"], set())
        if status_text not in legal:
            return {"error": f"illegal transition {issue['status']} -> {status_text}",
                    "allowed": sorted(legal)}
        previous = issue["status"]
        issue["status"] = status_text
        issue["updated"] = _as_of().isoformat()
        issue.setdefault("changelog", []).append(
            _change_now("platform-agent", "status", previous, status_text))
        snapshot = compact_issue(issue)
    return {"issue": snapshot, "transitioned": True}


def add_comment(issue_id: str, body: str) -> dict[str, Any]:
    checked, err = _need(issue_id, "issue_id")
    if err:
        return err
    text = (body or "").strip()
    if not text:
        return {"error": "body is required"}
    with _LOCK:
        issue = _find_issue(checked)
        if issue is None:
            return {"error": f"issue '{issue_id}' not found"}
        issue.setdefault("comments", []).append({
            "author": "platform-agent", "body": text,
            "date": _as_of().isoformat(), "created": _as_of().isoformat(),
        })
        issue["updated"] = _as_of().isoformat()
        count = len(issue["comments"])
    return {"issue_id": issue["id"], "comment_added": True, "comment_count": count}


def reset_fixtures() -> dict[str, Any]:
    reset_state()
    return {"reset": True, "issues": len(ISSUES), "sprints": len(SPRINTS),
            "projects": len(PROJECTS)}




def _status_counts(cards: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        status = card.get("status", "Unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _grade(*, at_risk: bool, elevated: bool) -> str:
    if at_risk:
        return "At Risk"
    if elevated:
        return "Elevated"
    return "Healthy"


def _parse_day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _change_now(author: str, field: str, frm: str, to: str) -> dict[str, Any]:
    return {"at": _as_of().isoformat(), "author": author, "field": field,
            "from": frm, "to": to}


def dispatch(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test/helper entry point: invoke a tool implementation by name."""
    args = args or {}
    table = {
        "jira_get_projects": lambda: get_projects(),
        "jira_get_project": lambda: get_project(
            args.get("project", ""), args.get("projects", "")),
        "jira_get_board": lambda: get_board(
            args.get("board_id", ""), args.get("board_ids", "")),
        "jira_get_sprints": lambda: get_sprints(
            args.get("board_id", ""), args.get("state", "closed"),
            args.get("project", ""), args.get("last_n", 0),
            args.get("include_metrics", False),
            args.get("board_ids", ""), args.get("projects", "")),
        "jira_get_sprint": lambda: get_sprint(
            args.get("sprint_id", ""), args.get("sprint_ids", ""),
            args.get("last_n", 0)),
        "jira_get_sprint_issues": lambda: get_sprint_issues(
            args.get("sprint_id", ""), args.get("max_results", 50),
            args.get("start_at", 0), args.get("sprint_ids", ""),
            args.get("last_n", 0), args.get("issue_type", ""),
            args.get("state", "closed")),
        "jira_get_sprint_report": lambda: get_sprint_report(
            args.get("sprint_id", ""), args.get("sprint_ids", ""),
            args.get("last_n", 0)),
        "jira_search": lambda: search_issues(
            args.get("jql", ""), args.get("max_results", 20), args.get("start_at", 0)),
        "jira_get_issue": lambda: get_issue(
            args.get("issue_id", ""), args.get("issue_ids", "")),
        "jira_get_worklogs": lambda: get_worklogs(
            args.get("issue_id", ""), args.get("issue_ids", "")),
        "jira_get_issue_comments": lambda: get_issue_comments(
            args.get("issue_id", ""), args.get("issue_ids", "")),
        "jira_get_issue_history": lambda: get_issue_history(
            args.get("issue_id", ""), args.get("issue_ids", "")),
        "jira_get_users": lambda: get_users(
            args.get("project"), args.get("projects", "")),
        "jira_get_team_members": lambda: get_team_members(
            args.get("project", ""), args.get("projects", "")),
        "jira_get_team_workload": lambda: get_team_workload(
            args.get("project"), args.get("sprint_id"),
            args.get("projects", ""), args.get("sprint_ids", ""),
            args.get("last_n", 0)),
        "jira_get_releases": lambda: get_releases(
            args.get("project", ""), args.get("projects", "")),
        "jira_get_release_status": lambda: get_release_status(
            args.get("project", ""), args.get("version", ""),
            args.get("versions", ""), args.get("projects", "")),
        "jira_get_project_health": lambda: get_project_health(
            args.get("project", ""), args.get("projects", "")),
        "jira_get_blocked_issues": lambda: get_blocked_issues(
            args.get("project"), args.get("projects", "")),
        "jira_get_overdue_issues": lambda: get_overdue_issues(
            args.get("project"), args.get("projects", "")),
        "jira_get_stuck_issues": lambda: get_stuck_issues(
            args.get("project"), args.get("days", 7), args.get("projects", "")),
        "jira_get_dependencies": lambda: get_dependencies(
            args.get("issue_id"), args.get("project"),
            args.get("issue_ids", ""), args.get("projects", "")),
        "jira_get_epics": lambda: get_epics(
            args.get("project", ""), args.get("projects", "")),
        "jira_get_backlog": lambda: get_backlog(
            args.get("project", ""), args.get("max_results", 20),
            args.get("start_at", 0), args.get("projects", "")),
        "jira_get_recent_activity": lambda: get_recent_activity(
            args.get("project"), args.get("days", 7),
            args.get("max_results", 20), args.get("projects", "")),
        "jira_create_issue": lambda: create_issue(
            args.get("project", ""), args.get("issue_type", ""), args.get("summary", "")),
        "jira_update_issue": lambda: update_issue(
            args.get("issue_id", ""), args.get("summary", "")),
        "jira_transition_issue": lambda: transition_issue(
            args.get("issue_id", ""), args.get("status", "")),
        "jira_add_comment": lambda: add_comment(
            args.get("issue_id", ""), args.get("body", "")),
        "jira_reset_fixtures": reset_fixtures,
    }
    fn = table.get(tool_name)
    if fn is None:
        return {"error": f"unknown tool '{tool_name}'"}
    return fn()
