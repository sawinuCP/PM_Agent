"""Extract named entities (project keys, issue keys) from goals and tasks."""

from __future__ import annotations

import re

_PROJECT_KEY_RE = re.compile(r"\b(PLAT|NOVA|ANLT|PAY)\b", re.IGNORECASE)
_ISSUE_KEY_RE = re.compile(r"\b([A-Z]{2,10}-\d+)\b")


def project_keys_in_text(text: str) -> list[str]:
    """Stable-order unique project keys mentioned in text."""
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _PROJECT_KEY_RE.finditer(text or ""):
        key = match.group(1).upper()
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def issue_keys_in_text(text: str) -> list[str]:
    return list(dict.fromkeys(_ISSUE_KEY_RE.findall(text or "")))


def step_scope_entities(*, task: str, goal: str) -> list[str]:
    """Entities this step is responsible for (task first, then goal)."""
    from_task = project_keys_in_text(task)
    if from_task:
        return from_task
    return project_keys_in_text(goal)
