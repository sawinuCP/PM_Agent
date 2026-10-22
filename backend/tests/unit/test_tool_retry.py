"""Unit tests for worker tool-error recovery (semantic retry)."""

from __future__ import annotations

from app.orchestration.tooling.tool_retry import (
    error_attempt_count,
    finish_blocked_on_tool_errors,
    suggest_recovery_calls,
    unresolved_tool_errors,
)


def _release_error_hop(project: str = "PLAT") -> dict:
    return {
        "type": "tool_result",
        "tool": "jira_get_release_status",
        "status": "error",
        "args": {"project": project},
        "data": {"error": "version is required"},
    }


def test_unresolved_tool_error_until_success() -> None:
    hops = [
        _release_error_hop("PLAT"),
        {
            "type": "tool_result",
            "tool": "jira_get_release_status",
            "status": "ok",
            "args": {"project": "PLAT", "version": "2.5.0"},
        },
    ]
    assert unresolved_tool_errors(hops) == []


def test_finish_blocked_on_unresolved_release_error() -> None:
    hops = [_release_error_hop("NOVA")]
    reason = finish_blocked_on_tool_errors(hops)
    assert reason is not None
    assert "jira_get_release_status" in reason
    assert "version is required" in reason


def test_suggest_releases_before_release_status() -> None:
    suggestions = suggest_recovery_calls(_release_error_hop("PLAT"))
    assert suggestions
    assert suggestions[0]["tool"] == "jira_get_releases"
    assert suggestions[0]["args"]["project"] == "PLAT"


def test_error_attempt_count_tracks_failures() -> None:
    args = {"project": "PLAT"}
    hops = [_release_error_hop("PLAT"), _release_error_hop("PLAT")]
    assert error_attempt_count(hops, "jira_get_release_status", args) == 2


def test_blocks_after_error_retry_budget_exhausted() -> None:
    from app.orchestration.graph.worker import _tool_call_block_reason

    args = {"project": "PLAT"}
    hops = [_release_error_hop("PLAT"), _release_error_hop("PLAT")]
    seen: set[str] = set()
    assert _tool_call_block_reason("jira_get_release_status", args, seen, hops) is not None
