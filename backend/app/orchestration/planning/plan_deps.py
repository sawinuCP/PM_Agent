"""Normalize plan DAG dependencies for parallel fetch → join workflows.

When the planner creates multiple root steps (parallel fetches), downstream
join/compare/compute steps must depend on every root — not just one sibling.
"""

from __future__ import annotations

import re

from app.models.plan import Plan
from app.orchestration.coverage.entity_scope import project_keys_in_text

_JOIN_TASK_RE = re.compile(
    r"\b(compare|comparison|merge|combine|both|versus|\bvs\b|side\s+by\s+side|"
    r"across|relative|contrast|difference|differences|parallel)\b",
    re.IGNORECASE,
)
_COMPUTE_AGENTS = frozenset({"data-scientist"})


def _root_step_ids(plan: Plan) -> list[str]:
    return [step.id for step in plan.steps if not step.depends_on]


def _needs_all_roots(step, *, goal: str, synthesis: str, root_set: set[str]) -> bool:
    if step.id in root_set:
        return False
    task_text = f"{step.task} {goal} {synthesis}"
    if _JOIN_TASK_RE.search(step.task or "") or _JOIN_TASK_RE.search(synthesis or ""):
        return True
    if step.agent in _COMPUTE_AGENTS and step.depends_on:
        return True
    if len(project_keys_in_text(task_text)) >= 2 and step.depends_on:
        return True
    return bool(step.depends_on and step.depends_on & root_set and len(root_set) >= 2)


def normalize_join_dependencies(plan: Plan) -> None:
    """Ensure join/compute steps wait for all parallel fetch roots when needed."""
    roots = _root_step_ids(plan)
    if len(roots) < 2:
        return
    root_set = set(roots)

    for step in plan.steps:
        if not _needs_all_roots(
            step, goal=plan.goal, synthesis=plan.synthesis or "", root_set=root_set,
        ):
            continue
        merged = set(step.depends_on) | root_set
        merged.discard(step.id)
        step.depends_on = sorted(merged)
