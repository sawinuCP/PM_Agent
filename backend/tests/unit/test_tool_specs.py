"""MCP to OpenAI tool spec conversion tests."""

from __future__ import annotations

from app.ai.tool_specs import (
    catalog_entry_to_openai_tool,
    normalize_parameters,
    shortlist_tool_names,
)


def test_normalize_parameters_defaults_to_object():
    assert normalize_parameters(None) == {"type": "object", "properties": {}}


def test_catalog_entry_to_openai_tool():
    spec = catalog_entry_to_openai_tool({
        "name": "jira_get_projects",
        "description": "List Jira projects",
        "input_schema": {"type": "object", "properties": {}},
    })
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "jira_get_projects"
    assert "List Jira projects" in spec["function"]["description"]


def test_shortlist_ranks_by_relevance_not_hardcoded_rules():
    catalog = {
        "jira_get_projects": {
            "name": "jira_get_projects",
            "description": "List all Jira projects",
        },
        "jira_get_sprint_issues": {
            "name": "jira_get_sprint_issues",
            "description": "Issues in a sprint",
        },
        "jira_get_users": {
            "name": "jira_get_users",
            "description": "Directory of users",
        },
    }
    names = ["jira_get_users", "jira_get_sprint_issues", "jira_get_projects"]
    picked = shortlist_tool_names(
        names,
        catalog,
        goal="What Jira projects exist?",
        task="List projects",
        max_tools=2,
    )
    assert len(picked) == 2
    assert "jira_get_projects" in picked


def test_shortlist_keeps_all_when_under_cap():
    catalog = {
        "a": {"name": "a", "description": "alpha"},
        "b": {"name": "b", "description": "beta"},
    }
    names = ["a", "b"]
    assert shortlist_tool_names(names, catalog, goal="x", task="y", max_tools=8) == names
