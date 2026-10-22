"""Tests for tool chain hints and arg enrichment."""

from __future__ import annotations

from app.orchestration.tooling.arg_enrich import enrich_tool_args
from app.orchestration.tooling.tool_chains import (
    chain_gaps,
    discovery_tools_for_task,
    suggest_chain_calls,
)


def test_discovery_tools_pinned_after_detail_attempt() -> None:
    pinned = discovery_tools_for_task(
        "What are the open tickets in this sprint?",
        {"jira_get_sprint_issues"},
    )
    assert "jira_get_sprints" in pinned
    assert not discovery_tools_for_task("List tickets", set())


def test_suggest_chain_before_sprint_issues() -> None:
    suggestions = suggest_chain_calls(
        "List sprint issues for PLAT",
        hops=[],
        bound_tools={"jira_get_sprints", "jira_get_sprint_issues"},
    )
    assert suggestions
    assert suggestions[0]["tool"] == "jira_get_sprints"


def test_enrich_sprint_id_from_prior_hops() -> None:
    hops = [{
        "type": "tool_result",
        "status": "ok",
        "tool": "jira_get_sprints",
        "data": {"sprints": [{"id": "SPR-42", "board_id": "b1"}]},
    }]
    args = enrich_tool_args("jira_get_sprint_issues", {}, hops)
    assert args["sprint_id"] == "SPR-42"


def test_chain_gap_when_detail_without_discovery() -> None:
    hops = [{
        "type": "tool_result",
        "status": "ok",
        "tool": "jira_get_sprint_issues",
        "args": {},
        "data": {},
    }]
    gaps = chain_gaps(
        "open tickets in this sprint",
        hops,
        bound_tools={"jira_get_sprints", "jira_get_sprint_issues"},
    )
    assert gaps
