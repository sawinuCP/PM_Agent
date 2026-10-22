"""Tool harvest hint tests."""

from __future__ import annotations

from app.orchestration.tooling.tool_harvest import (
    discovered_entities,
    harvest_context,
    suggest_batch_calls,
)


def test_discovered_projects_from_get_projects_hop() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "jira_get_projects",
        "status": "ok",
        "data": {
            "projects": [
                {"key": "PLAT", "name": "Platform"},
                {"key": "NOVA", "name": "Nova"},
            ],
        },
    }]
    found = discovered_entities(hops)
    assert found["projects"] == ["PLAT", "NOVA"]


def test_suggest_batch_when_multi_project_velocity_task() -> None:
    discovered = {"projects": ["PLAT", "NOVA", "ANLT"], "sprints": [], "boards": []}
    task = "Query all accessible projects and list closed sprints for velocity forecast"
    suggestions = suggest_batch_calls(discovered, task=task)
    assert suggestions
    assert suggestions[0]["tool"] == "jira_get_sprints"
    assert "PLAT,NOVA,ANLT" in suggestions[0]["args"]["projects"]


def test_harvest_context_empty_without_hops() -> None:
    assert harvest_context([], task="fetch sprints") is None
