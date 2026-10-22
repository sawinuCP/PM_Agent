"""Unit tests for L1 system prompt trimming (semantic-only shrink)."""

from __future__ import annotations

import logging

from app.agents.worker import count_tokens, trim_system_prompt
from app.ai.prompts import CONTEXT_BLOCK, HISTORICAL_HINT_WRAPPER, WORKER_SYSTEM_ENVELOPE


def _envelope(*, task: str = "Fetch data", context: str = "") -> str:
    return WORKER_SYSTEM_ENVELOPE.format(
        agent_name="jira-analyst",
        step_id="s1",
        task=task,
        context_block=context,
    )


def test_no_trim_when_under_budget() -> None:
    role = "You are a Jira analyst."
    result = trim_system_prompt(
        8000,
        role=role,
        agent_name="jira-analyst",
        step_id="s1",
        task="Fetch PLAT health",
    )
    assert "Assigned task: Fetch PLAT health" in result
    assert role in result


def test_upstream_never_shrunk_when_over_budget() -> None:
    role = "ROLE-START " + ("x" * 200)
    upstream = CONTEXT_BLOCK.format(upstream_digests="U" * 8000)
    budget = count_tokens(role + "\n\n" + _envelope(context=upstream)) - 50
    result = trim_system_prompt(
        budget,
        role=role,
        agent_name="data-scientist",
        step_id="s2",
        task="Analyze upstream",
        protected_blocks=[("upstream", upstream)],
    )
    assert role == result.split("\n\n")[0] or role in result
    assert "U" * 100 in result
    assert "Assigned task: Analyze upstream" in result


def test_semantic_shrinks_before_over_budget_warning() -> None:
    role = "Analyst role."
    upstream = CONTEXT_BLOCK.format(upstream_digests="UPSTREAM-MARKER")
    semantic = HISTORICAL_HINT_WRAPPER.format(
        hint_text='{"type": "semantic_facts", "facts": ["Fact one"]}',
    )
    combined = f"{role}\n\n" + _envelope(context=f"{upstream}\n{semantic}")
    budget = count_tokens(combined) - count_tokens(semantic) + 50
    result = trim_system_prompt(
        budget,
        role=role,
        agent_name="jira-analyst",
        step_id="s1",
        task="Compare",
        protected_blocks=[("upstream", upstream)],
        shrinkable_blocks=[("semantic", semantic)],
    )
    assert "UPSTREAM-MARKER" in result
    assert role in result
    assert count_tokens(result) <= budget + 20 or "Fact one" not in result


def test_over_budget_keeps_role_not_emergency_flat_trim(caplog) -> None:
    role = "Role text preserved."
    upstream = CONTEXT_BLOCK.format(upstream_digests="Z" * 12000)
    budget = 500
    with caplog.at_level(logging.WARNING):
        result = trim_system_prompt(
            budget,
            role=role,
            agent_name="jira-analyst",
            step_id="s1",
            task="Big upstream task",
            protected_blocks=[("upstream", upstream)],
        )
    assert "Assigned task: Big upstream task" in result
    assert role in result
    assert not result.endswith("...[trimmed to fit token budget]")
    assert any("L1 over budget after semantic trim" in r.message for r in caplog.records)
