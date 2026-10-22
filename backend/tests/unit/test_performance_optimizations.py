"""Tests for planner join deps and task-pattern helpers."""

from __future__ import annotations

from app.models.plan import Plan, PlanStep
from app.orchestration.coverage.entity_coverage import entity_finish_blocked_reason
from app.orchestration.coverage.task_patterns import (
    is_analysis_compute_task,
    is_issue_lookup_task,
    is_project_list_task,
    is_sprint_history_task,
)
from app.orchestration.planning.plan_deps import normalize_join_dependencies


def test_sprint_history_task_detection() -> None:
    assert is_sprint_history_task("Predict velocity from previous Jira sprints")
    assert not is_sprint_history_task("Hi there")


def test_issue_lookup_task_detection() -> None:
    assert is_issue_lookup_task("What is the status of PLAT-9?")
    assert not is_issue_lookup_task("List all projects")


def test_entity_finish_uses_local_hops_only() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "args": {"project": "PLAT", "include_metrics": True},
        "data": {"sprints": [{"id": "SPR-3", "bug_count": 1}]},
    }]
    task = "Analyze sprint bug trend from upstream retrieved data"
    assert entity_finish_blocked_reason(
        task=task, goal="Compare bug counts", hops=[], upstream_hops=hops,
    ) is not None
    assert entity_finish_blocked_reason(
        task=task, goal="Compare bug counts", hops=hops, upstream_hops=[],
    ) is None


def test_normalize_join_waits_for_all_parallel_roots() -> None:
    plan = Plan(
        thread_id="t1",
        goal="Compare PLAT and NOVA health side by side",
        reasoning=["parallel fetch"],
        steps=[
            PlanStep(id="s1", agent="jira-analyst", task="Fetch PLAT health", depends_on=[]),
            PlanStep(id="s2", agent="jira-analyst", task="Fetch NOVA health", depends_on=[]),
            PlanStep(
                id="s3", agent="data-scientist",
                task="Compare health metrics from upstream steps",
                depends_on=["s1"],
            ),
        ],
        synthesis="Compare both projects for the user.",
    )
    normalize_join_dependencies(plan)
    assert set(plan.steps[2].depends_on) == {"s1", "s2"}


def test_project_list_task_detection() -> None:
    assert is_project_list_task("What Jira projects exist and who leads each?")


def test_analysis_compute_task_generic() -> None:
    assert is_analysis_compute_task("Run statistical analysis on fetched sprint data")
