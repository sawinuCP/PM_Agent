"""Unit tests for compact tool hop helpers."""

from __future__ import annotations

import asyncio

from app.mcp_integration.digest import structured_preview
from app.orchestration.tooling.hop_evidence import (
    build_tool_hop,
    hop_data_from_outcome,
    hydrate_hop_data,
    prior_hops_for_worker,
)


def test_hop_data_from_outcome_uses_preview_not_payload() -> None:
    full = {
        "sprints": [
            {"id": "SPR-3", "bug_count": 2, "name": "Sprint 3"},
            {"id": "SPR-4", "bug_count": 0},
        ],
    }
    preview = structured_preview(full)
    data = hop_data_from_outcome({"payload": full, "preview": preview, "status": "ok"})
    assert "sprints_count" in data or preview == data
    assert data.get("sprints") is None or len(data.get("sprints") or []) <= 20


def test_build_tool_hop_carries_preview() -> None:
    full = {"projects": [{"key": "PLAT", "board_id": "BRD-1"}]}
    preview = structured_preview(full)
    hop = build_tool_hop(
        tool_name="jira_get_projects",
        args={},
        outcome={"preview": preview, "status": "ok", "result_id": "r1", "digest": "ok"},
    )
    assert hop["data"]["projects_count"] == 1 or hop["data"].get("projects")
    assert hop["result_id"] == "r1"


def test_prior_hops_for_worker_windows_recent() -> None:
    hops = [{"type": "tool_result", "tool": f"t{i}"} for i in range(10)]
    windowed = prior_hops_for_worker(hops)
    assert len(windowed) == 8
    assert windowed[0]["tool"] == "t2"


def test_hydrate_hop_data_is_noop() -> None:
    hop = {
        "type": "tool_result",
        "tool": "jira_get_sprints",
        "status": "ok",
        "result_id": "abc",
        "data": {"sprints_count": 1},
    }
    hydrated = asyncio.run(hydrate_hop_data(hop))
    assert hydrated == hop
