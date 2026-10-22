"""Task coverage gate tests."""

from __future__ import annotations

from app.orchestration.coverage import (
    coverage_gaps,
    finish_blocked_reason,
    task_requires_multi_entity_scope,
)


def _projects_hop(keys: list[str]) -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_projects",
        "status": "ok",
        "data": {
            "projects": [{"key": key, "name": key} for key in keys],
        },
    }


def _sprint_hop(*, project: str = "", board_id: str = "") -> dict:
    args: dict = {}
    if project:
        args["project"] = project
    if board_id:
        args["board_id"] = board_id
    return {
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "args": args,
        "data": {"sprints": []},
    }


def test_simple_task_not_multi_scope() -> None:
    task = "What Jira projects exist?"
    assert not task_requires_multi_entity_scope(task)
    assert coverage_gaps(task, []) == []


def test_multi_entity_without_results_blocked() -> None:
    task = "Query all accessible projects and list closed sprints for velocity"
    gaps = coverage_gaps(task, [])
    assert gaps
    assert "No successful tool results" in gaps[0]


def test_sprint_velocity_incomplete_when_one_of_many_fetched() -> None:
    task = (
        "Query all accessible projects and fetch closed sprint history "
        "for velocity forecast across every board"
    )
    hops = [
        _projects_hop(["PLAT", "NOVA", "ANLT"]),
        _sprint_hop(board_id="BRD-1"),
    ]
    gaps = coverage_gaps(task, hops)
    assert gaps
    assert "Sprint history incomplete" in gaps[0]


def test_sprint_velocity_ok_when_all_projects_fetched() -> None:
    task = (
        "Query all accessible projects and fetch closed sprint history "
        "for velocity forecast across every board"
    )
    hops = [
        _projects_hop(["PLAT", "NOVA"]),
        _sprint_hop(project="PLAT"),
        _sprint_hop(project="NOVA"),
    ]
    assert coverage_gaps(task, hops) == []
    assert finish_blocked_reason(task, hops) is None


def test_batch_projects_arg_satisfies_sprint_coverage() -> None:
    task = "Fetch sprint history for all projects to compute velocity"
    hops = [
        _projects_hop(["PLAT", "NOVA", "ANLT"]),
        {
            "type": "tool_result",
            "tool": "jira_get_sprints",
            "status": "ok",
            "args": {"projects": "PLAT,NOVA,ANLT", "last_n": 5},
            "data": {"sprints": []},
        },
    ]
    assert coverage_gaps(task, hops) == []
