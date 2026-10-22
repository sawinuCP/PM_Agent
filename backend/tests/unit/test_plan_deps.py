"""Tests for plan dependency normalization."""

from __future__ import annotations

from app.models.schemas import Plan, PlanStep
from app.orchestration.planning.plan_deps import normalize_join_dependencies


def test_join_step_waits_for_all_parallel_roots() -> None:
    plan = Plan(
        thread_id="t",
        goal="Compare PLAT and NOVA health",
        steps=[
            PlanStep(id="s1", agent="jira-analyst", task="Fetch PLAT health", depends_on=[]),
            PlanStep(id="s2", agent="jira-analyst", task="Fetch NOVA health", depends_on=[]),
            PlanStep(
                id="s3", agent="data-scientist",
                task="Compare PLAT vs NOVA health metrics",
                depends_on=["s1"],
            ),
        ],
        synthesis="Compare both projects",
    )
    normalize_join_dependencies(plan)
    assert set(plan.steps[2].depends_on) == {"s1", "s2"}


def test_velocity_plan_unchanged_single_root() -> None:
    plan = Plan(
        thread_id="t",
        goal="Predict velocity",
        steps=[
            PlanStep(id="s1", agent="jira-analyst", task="Fetch sprint history", depends_on=[]),
            PlanStep(
                id="s2", agent="data-scientist",
                task="Forecast velocity from sprint history",
                depends_on=["s1"],
            ),
        ],
        synthesis="Velocity forecast",
    )
    normalize_join_dependencies(plan)
    assert plan.steps[1].depends_on == ["s1"]
