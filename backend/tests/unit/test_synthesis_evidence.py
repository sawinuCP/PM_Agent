"""Synthesis evidence builder tests."""

from __future__ import annotations

import json

from app.mcp_integration.digest import build_synthesis_evidence


def test_build_synthesis_evidence_extracts_tool_rows() -> None:
    packet = {
        "type": "step_result",
        "step": "s1",
        "summary": "Fetched PLAT health",
        "hops": [
            {
                "type": "tool_result",
                "tool": "jira_get_project_health",
                "status": "ok",
                "result_id": "tr-1",
                "args": {"project": "PLAT"},
                "data": {"project": "PLAT", "open_issues": 14},
            },
            {
                "type": "reject",
                "tool": "jira_search",
                "status": "rejected",
            },
        ],
    }
    evidence = build_synthesis_evidence({"s1": json.dumps(packet)})
    assert len(evidence) == 1
    assert evidence[0]["step"] == "s1"
    assert len(evidence[0]["tool_evidence"]) == 1
    assert evidence[0]["tool_evidence"][0]["tool"] == "jira_get_project_health"
    assert evidence[0]["tool_evidence"][0]["result_id"] == "tr-1"
    assert evidence[0]["tool_evidence"][0]["data"]["project"] == "PLAT"


def test_build_synthesis_evidence_preserves_step_order() -> None:
    outputs = {
        "s2": json.dumps({"type": "step_result", "step": "s2", "summary": "b", "hops": []}),
        "s1": json.dumps({"type": "step_result", "step": "s1", "summary": "a", "hops": []}),
    }
    steps = [row["step"] for row in build_synthesis_evidence(outputs)]
    assert steps == ["s1", "s2"]
