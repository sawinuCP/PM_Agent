"""Unit tests for entity-scoped validation and coverage gates."""

from __future__ import annotations

import json

from app.orchestration.coverage.entity_coverage import (
    entity_finish_blocked_reason,
    entities_with_blocked_evidence,
    entities_with_health_evidence,
    entities_with_sprint_metrics_evidence,
    synthesis_entity_gaps,
)
from app.orchestration.coverage.entity_scope import project_keys_in_text, step_scope_entities
from app.orchestration.tooling.tool_validate import validate_tool_call


def _health_hop(project: str) -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_project_health",
        "status": "ok",
        "args": {"project": project},
        "data": {"project": project, "overall": "At Risk"},
    }


def _blocked_hop(project: str) -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_blocked_issues",
        "status": "ok",
        "args": {"project": project},
        "data": {"project": project, "count": 2, "issues": []},
    }


def _sprints_hop(project: str, sprint_id: str = "SPR-6") -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "args": {"project": project, "state": "active"},
        "data": {
            "sprints": [{"id": sprint_id, "state": "active", "name": "Sprint 6"}],
        },
    }


def _sprint_report_hop(sprint_id: str = "SPR-6") -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_sprint_report",
        "status": "ok",
        "args": {"sprint_id": sprint_id},
        "data": {
            "sprint_id": sprint_id,
            "committed_points": 24,
            "issue_points_done": 8,
            "days_remaining": 9,
        },
    }


def test_project_keys_from_task() -> None:
    assert project_keys_in_text("Fetch NOVA project health metrics") == ["NOVA"]
    assert step_scope_entities(
        task="Fetch PLAT project health metrics",
        goal="Compare PLAT and NOVA",
    ) == ["PLAT"]


def test_entity_finish_blocked_when_scope_missing() -> None:
    task = "Fetch NOVA project health metrics"
    reason = entity_finish_blocked_reason(task=task, goal="", hops=[])
    assert reason is not None
    assert "NOVA" in reason


def test_entity_finish_ok_when_health_present() -> None:
    task = "Fetch PLAT project health metrics"
    hops = [_health_hop("PLAT")]
    assert entities_with_health_evidence(hops) == {"PLAT"}
    assert entity_finish_blocked_reason(task=task, goal="", hops=hops) is None


def test_entity_finish_blocked_for_sprint_metrics() -> None:
    task = "Fetch NOVA active sprint progress"
    reason = entity_finish_blocked_reason(task=task, goal="", hops=[])
    assert reason is not None
    assert "sprint metrics" in reason


def test_sprint_metrics_evidence() -> None:
    task = "Fetch PLAT active sprint progress"
    hops = [_sprints_hop("PLAT"), _sprint_report_hop("SPR-6")]
    assert entities_with_sprint_metrics_evidence(hops) == {"PLAT"}
    assert entity_finish_blocked_reason(task=task, goal="", hops=hops) is None


def test_blocked_evidence() -> None:
    hops = [_blocked_hop("NOVA")]
    assert entities_with_blocked_evidence(hops) == {"NOVA"}


def test_validate_rejects_wrong_project_scope() -> None:
    reason = validate_tool_call(
        "jira_get_project_health",
        {"project": "ANLT"},
        schema={"required": ["project"], "properties": {"project": {"type": "string"}}},
        task="Fetch NOVA project health metrics",
        goal="",
    )
    assert reason is not None
    assert "scope" in reason


def test_validate_requires_project_for_scoped_health() -> None:
    reason = validate_tool_call(
        "jira_get_project_health",
        {},
        schema={"required": ["project"], "properties": {"project": {"type": "string"}}},
        task="Fetch NOVA project health metrics",
        goal="",
    )
    assert reason is not None
    assert "project" in reason


def test_synthesis_entity_gaps_detects_missing_project() -> None:
    packet = {
        "type": "step_result",
        "step": "s1",
        "summary": "PLAT only",
        "hops": [_health_hop("PLAT")],
    }
    gaps = synthesis_entity_gaps(
        "Compare PLAT and NOVA project health side by side",
        {"s1": json.dumps(packet)},
    )
    assert gaps == ["NOVA"]
