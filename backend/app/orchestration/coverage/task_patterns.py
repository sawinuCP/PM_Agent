"""Shared task-kind heuristics for entity coverage gates."""

from __future__ import annotations

import re

_HEALTH_FETCH_RE = re.compile(
    r"\b(fetch|retrieve|get|load|pull)\b.*\b(health|metrics|status)\b",
    re.IGNORECASE,
)
_HEALTH_TASK_RE = re.compile(
    r"\b(health|on\s+track|at\s+risk|project\s+health)\b",
    re.IGNORECASE,
)
_BLOCKED_TASK_RE = re.compile(r"\bblocked\b", re.IGNORECASE)
_SPRINT_METRICS_TASK_RE = re.compile(
    r"\b(active\s+sprint|sprint\s+progress|committed\s+points|completed\s+points|"
    r"days\s+remaining|remaining\s+work|sprint\s+report|sprint\s+status)\b",
    re.IGNORECASE,
)
_SPRINT_HISTORY_RE = re.compile(
    r"\b(closed\s+sprint|velocity|velocities|historical|forecast|burndown|"
    r"last\s+\d+\s+sprints?|sprint\s+history)\b",
    re.IGNORECASE,
)
_BUG_COUNT_TASK_RE = re.compile(
    r"\b(bug\s+counts?|bugs?\s+(per|across|in)\s+sprint|bug-type|bug\s+trend|"
    r"defect\s+counts?|count\s+bugs?)\b",
    re.IGNORECASE,
)
_ANALYSIS_COMPUTE_RE = re.compile(
    r"\b(analy[sz]e|analysis|compute|calculat|statistic|regression|trend|"
    r"forecast|predict|model|aggregate|summariz)\b",
    re.IGNORECASE,
)
_PROJECT_LIST_RE = re.compile(
    r"\b(what\s+projects|list\s+projects|projects\s+exist|who\s+leads|project\s+leads?)\b",
    re.IGNORECASE,
)
_ISSUE_LOOKUP_RE = re.compile(
    r"\b(status|details|summary|info)\b.*\b(issue|ticket|bug)\b|"
    r"\b(issue|ticket)\b.*\b(status|details|summary)\b",
    re.IGNORECASE,
)
_LAST_N_SPRINTS_RE = re.compile(
    r"\blast\s+(\d+)\s+sprints?\b",
    re.IGNORECASE,
)


def is_health_task(text: str) -> bool:
    return bool(_HEALTH_FETCH_RE.search(text) or _HEALTH_TASK_RE.search(text))


def is_blocked_task(text: str) -> bool:
    return bool(_BLOCKED_TASK_RE.search(text))


def is_sprint_metrics_task(text: str) -> bool:
    if is_bug_count_task(text):
        return False
    if _SPRINT_HISTORY_RE.search(text):
        return False
    return bool(_SPRINT_METRICS_TASK_RE.search(text))


def is_bug_count_task(text: str) -> bool:
    return bool(_BUG_COUNT_TASK_RE.search(text))


def is_sprint_history_task(text: str) -> bool:
    """Velocity, burndown, or closed-sprint history fetches."""
    if is_bug_count_task(text):
        return False
    return bool(_SPRINT_HISTORY_RE.search(text))


def is_analysis_compute_task(text: str) -> bool:
    """Numerical / trend work typically done by data-scientist over fetched data."""
    return bool(_ANALYSIS_COMPUTE_RE.search(text))


def is_project_list_task(text: str) -> bool:
    return bool(_PROJECT_LIST_RE.search(text))


def is_issue_lookup_task(text: str) -> bool:
    from app.orchestration.coverage.entity_scope import issue_keys_in_text

    if not issue_keys_in_text(text):
        return False
    return bool(_ISSUE_LOOKUP_RE.search(text)) or bool(issue_keys_in_text(text))


def last_n_sprints_from_text(text: str, *, default: int = 3) -> int:
    match = _LAST_N_SPRINTS_RE.search(text or "")
    if not match:
        return default
    try:
        return max(1, min(int(match.group(1)), 12))
    except (TypeError, ValueError):
        return default


def task_text(*, task: str, goal: str) -> str:
    return f"{task} {goal}".strip()
