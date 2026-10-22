"""Planner unit tests — catalog + plan validation (agent-owned tools)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.ai.provider import MockLLM
from app.core.errors import PlanValidationError
from app.models.schemas import Plan, PlanStep
from app.orchestration.planning.planner import PlanningContext, create_plan, planner_catalog
from app.orchestration.planning.plan_tools import sanitize_plan, validate_plan
from tests.fakes import FakeMCPManager


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def test_planner_catalog_lists_agents(registry) -> None:
    manager = FakeMCPManager()
    menu = planner_catalog(registry, manager)
    dumped = json.dumps(menu)
    assert "agents" in menu
    assert "tools" not in menu
    assert "jira-analyst" in dumped


def test_validate_plan_rejects_unknown_agent(registry) -> None:
    allowed = {entry.name: list(entry.tools) for entry in registry.capability_catalog()}
    plan = Plan(
        thread_id="t",
        goal="g",
        steps=[PlanStep(id="s1", agent="unknown-agent", task="fetch something")],
        synthesis="s",
    )
    with pytest.raises(PlanValidationError, match="unknown agent"):
        validate_plan(plan, allowed_by_agent=allowed)


def test_sanitize_plan_clears_planner_tool_hints(registry) -> None:
    allowed = {entry.name: list(entry.tools) for entry in registry.capability_catalog()}
    plan = Plan(
        thread_id="t",
        goal="g",
        steps=[PlanStep(
            id="s1", agent="jira-analyst", task="List projects",
            tools=["jira_get_projects", "fake_tool_xyz"],
        )],
        synthesis="s",
    )
    sanitize_plan(plan, allowed_by_agent=allowed)
    assert plan.steps[0].tools == []
    assert plan.steps[0].arg_bindings == {}
    assert plan.steps[0].arg_resolution == []


def test_sanitize_plan_clears_arg_bindings(registry) -> None:
    allowed = {entry.name: list(entry.tools) for entry in registry.capability_catalog()}
    plan = Plan(
        thread_id="t",
        goal="g",
        steps=[PlanStep(
            id="s1", agent="jira-analyst", task="List projects",
            arg_bindings={"jira_search": {"jql": "project = PLAT"}},
            arg_resolution=["prior_step"],
        )],
        synthesis="s",
    )
    sanitize_plan(plan, allowed_by_agent=allowed)
    assert plan.steps[0].arg_bindings == {}
    assert plan.steps[0].arg_resolution == []


def test_create_plan_accepts_mock_llm_output(registry) -> None:
    manager = FakeMCPManager()
    plan, used_llm = _run(create_plan(
        PlanningContext(thread_id="t", goal="List available projects"),
        MockLLM(), registry, manager))
    assert used_llm is True
    assert plan is not None
    assert len(plan.steps) >= 1
    assert plan.steps[0].tools == []


def test_create_plan_velocity_uses_two_agents_via_llm(registry) -> None:
    manager = FakeMCPManager()
    plan, used_llm = _run(create_plan(
        PlanningContext(
            thread_id="t",
            goal="Predict next sprint velocity from previous Jira sprints",
        ),
        MockLLM(), registry, manager))
    assert used_llm is True
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].agent == "jira-analyst"
    assert plan.steps[1].agent == "data-scientist"
    assert plan.steps[1].depends_on == ["s1"]
    assert not plan.clarify_question
