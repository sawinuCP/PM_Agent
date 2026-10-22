"""Worker tool-call deduplication guards."""

from __future__ import annotations

from app.orchestration.graph.worker import (
    _call_signature,
    _successful_tool_names,
    _tool_call_block_reason,
)


def test_blocks_exact_duplicate_after_success() -> None:
    hops: list[dict] = [{
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "args": {"board_id": "BRD-1"},
    }]
    seen: set[str] = set()
    args = {"board_id": "BRD-1"}
    seen.add(_call_signature("jira_get_sprints", args))
    assert _tool_call_block_reason("jira_get_sprints", args, seen, hops) is not None


def test_allows_retry_same_args_after_error_until_budget() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "run_python",
        "status": "error",
        "args": {"code": "x=1"},
    }]
    seen: set[str] = set()
    assert _tool_call_block_reason("run_python", {"code": "x=1"}, seen, hops) is None


def test_allows_same_tool_with_different_args_after_success() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "args": {"project": "PLAT", "state": "closed"},
    }]
    seen: set[str] = set()
    seen.add(_call_signature("jira_get_sprints", {"project": "PLAT", "state": "closed"}))
    assert _tool_call_block_reason(
        "jira_get_sprints", {"project": "NOVA", "state": "closed"}, seen, hops) is None


def test_allows_retry_after_error_with_different_args() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "run_python",
        "status": "error",
        "args": {},
    }]
    seen: set[str] = set()
    assert _tool_call_block_reason("run_python", {"code": "x=1"}, seen, hops) is None


def test_allows_different_tools_after_success() -> None:
    hops = [{
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
    }]
    seen: set[str] = set()
    assert _tool_call_block_reason(
        "jira_get_sprint_issues", {"sprint_id": "SPR-6"}, seen, hops) is None


def test_successful_tool_names() -> None:
    hops = [
        {"type": "tool_result", "tool": "a", "status": "ok"},
        {"type": "tool_result", "tool": "b", "status": "error"},
        {"type": "reject", "tool": "c", "status": "rejected"},
    ]
    assert _successful_tool_names(hops) == {"a"}
