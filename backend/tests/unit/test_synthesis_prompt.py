"""Synthesis prompt composition tests."""

from __future__ import annotations

import json

from app.ai.prompts import SYNTHESIS_SYSTEM
from app.orchestration.graph.synthesis import compose_synthesis_prompt


def test_compose_synthesis_prompt_includes_structured_evidence() -> None:
    outputs = {
        "s1": json.dumps({
            "type": "step_result",
            "step": "s1",
            "summary": "done",
            "hops": [{
                "type": "tool_result",
                "tool": "jira_get_project_health",
                "status": "ok",
                "result_id": "tr-99",
                "args": {"project": "NOVA"},
                "data": {"project": "NOVA", "open_issues": 5},
            }],
        }),
    }
    system, user = compose_synthesis_prompt(
        goal="What about NOVA?",
        conversation="User: How healthy is PLAT?\nAssistant: PLAT has 14 open issues.",
        step_outputs=outputs,
    )
    assert system == SYNTHESIS_SYSTEM
    assert "What about NOVA?" in user
    assert "do not quote prior numbers" in user.lower()
    assert "NOVA" in user
    assert "rollup" in user
    assert "steps" in user
    assert "tr-99" in user


def test_pack_synthesis_evidence_preserves_cross_step_rollup() -> None:
    from app.mcp_integration.digest import pack_synthesis_evidence_json

    outputs = {
        "s1": json.dumps({
            "type": "step_result",
            "step": "s1",
            "summary": "PLAT sprints",
            "hops": [{
                "type": "tool_result",
                "tool": "jira_get_sprint_issues",
                "status": "ok",
                "args": {"project": "PLAT", "sprint_id": "SPR-3"},
                "data": {
                    "by_sprint": [
                        {"sprint_id": "SPR-3", "bug_count": 1, "issue_count": 4},
                        {"sprint_id": "SPR-4", "bug_count": 0, "issue_count": 2},
                    ],
                },
            }],
        }),
        "s4": json.dumps({
            "type": "step_result",
            "step": "s4",
            "summary": "NOVA sprints",
            "hops": [{
                "type": "tool_result",
                "tool": "jira_get_sprint_issues",
                "status": "ok",
                "args": {"project": "NOVA"},
                "data": {
                    "by_sprint": [
                        {"sprint_id": "NOVA-SPR-2", "bug_count": 1, "issue_count": 2},
                    ],
                },
            }],
        }),
    }
    packed = pack_synthesis_evidence_json(outputs, max_chars=8000)
    assert "SPR-3" in packed
    assert "NOVA-SPR-2" in packed
    assert "sprint_metrics" in packed or "by_sprint" in packed
