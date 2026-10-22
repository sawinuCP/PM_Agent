"""Ground truth from the in-memory Jira simulator (docs/TEST_DATASET.md §2)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

JIRA_DIR = Path(__file__).resolve().parents[1] / "mcp_servers" / "jira"
BACKEND_DIR = Path(__file__).resolve().parents[1]
for path in (BACKEND_DIR, JIRA_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dataset import PROJECTS, USERS  # noqa: E402
import service as jira_svc  # noqa: E402


def _init() -> None:
    jira_svc.reset_state()


def plat_closed_velocities() -> list[int]:
    _init()
    rows = jira_svc.get_sprints(board_id="BRD-1", state="closed").get("sprints") or []
    return [int(r.get("completed_points") or 0) for r in rows]


def plat_last_n_bug_counts(n: int = 3) -> list[dict[str, Any]]:
    _init()
    payload = jira_svc.get_sprints(
        board_id="BRD-1", state="closed", last_n=n, include_metrics=True,
    )
    rows = payload.get("sprints") or []
    return [
        {
            "id": str(r.get("id") or ""),
            "bug_count": int(r.get("bug_count") or 0),
            "completed_points": int(r.get("completed_points") or 0),
        }
        for r in rows
    ]


def project_leads() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, row in PROJECTS.items():
        lead_id = str(row.get("lead") or "")
        display = USERS.get(lead_id, {}).get("display_name") or lead_id
        out[key] = str(display)
    return out


def project_health(project: str) -> str:
    _init()
    row = jira_svc.get_project_health(project=project)
    if isinstance(row, dict):
        projects = row.get("projects")
        if isinstance(projects, list) and projects:
            return str(projects[0].get("overall") or projects[0].get("health") or "")
        return str(row.get("overall") or row.get("health") or "")
    return ""


def issue_facts(issue_id: str) -> dict[str, Any]:
    _init()
    row = jira_svc.get_issue(issue_id=issue_id)
    if not isinstance(row, dict) or row.get("error"):
        return {}
    issue = row.get("issue") if isinstance(row.get("issue"), dict) else row
    if not isinstance(issue, dict):
        return {}
    assignee = issue.get("assignee")
    if isinstance(assignee, dict):
        assignee_name = assignee.get("display_name")
    else:
        assignee_name = issue.get("assignee_name") or assignee
    blocked_by = issue.get("blocked_by") or []
    return {
        "key": issue.get("key") or issue.get("id") or issue_id,
        "summary": issue.get("summary") or "",
        "status": issue.get("status") or "",
        "type": issue.get("type") or issue.get("issue_type") or "",
        "assignee": assignee_name,
        "blocked": bool(blocked_by),
        "blocked_by": list(blocked_by),
    }


def velocity_forecast_band() -> tuple[int, int]:
    """Reasonable forecast range from closed PLAT velocities (mean ± ~20%)."""
    vals = plat_closed_velocities()
    if not vals:
        return 30, 45
    mean = sum(vals) / len(vals)
    lo = max(20, int(mean * 0.75))
    hi = int(mean * 1.35)
    return lo, hi


def extract_numbers(text: str) -> list[float]:
    return [float(m.group(0)) for m in re.finditer(r"\b\d+(?:\.\d+)?\b", text)]


def mentions_any(text: str, tokens: list[str]) -> bool:
    lower = text.lower()
    return any(t.lower() in lower for t in tokens)
