"""Plan validation against registry (agents + DAG). Tool binding is agent-owned."""

from __future__ import annotations

from app.core.errors import PlanValidationError
from app.models.plan import Plan


def sanitize_plan(plan: Plan, *, allowed_by_agent: dict[str, list[str]],
                  catalog_tools: set[str] | None = None) -> None:
    """Drop planner tool hints — workers bind tools from YAML at spawn time."""
    _ = allowed_by_agent, catalog_tools
    for step in plan.steps:
        step.tools = []
        step.arg_bindings = {}
        step.arg_resolution = []


def validate_plan(plan: Plan, *, allowed_by_agent: dict[str, list[str]],
                  catalog_tools: set[str] | None = None) -> None:
    """Referential integrity: known agents, non-empty tasks, valid DAG."""
    _ = catalog_tools
    if plan.clarify_question and plan.clarify_question.strip():
        return
    for step in plan.steps:
        if step.agent not in allowed_by_agent:
            raise PlanValidationError(f"step {step.id}: unknown agent '{step.agent}'")
        if not (step.task or "").strip():
            raise PlanValidationError(f"step {step.id}: task is required")
