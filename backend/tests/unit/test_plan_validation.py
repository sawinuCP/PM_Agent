"""Plan contract validation edge cases."""

from __future__ import annotations

import pytest
from app.models.schemas import Plan, PlanStep


def _plan(steps: list[PlanStep], **kwargs) -> Plan:
    return Plan(thread_id="t", goal="g", steps=steps,
                synthesis="synth", **kwargs)


def test_valid_sequential_plan() -> None:
    plan = _plan([
        PlanStep(id="s1", agent="jira-analyst", task="fetch", tools=[], depends_on=[]),
        PlanStep(id="s2", agent="data-scientist", task="compute", tools=[],
                 depends_on=["s1"]),
    ])
    assert [s.id for s in plan.in_order()] == ["s1", "s2"]


def test_rejects_duplicate_step_ids() -> None:
    with pytest.raises(Exception, match="duplicate"):
        _plan([
            PlanStep(id="s1", agent="a", task="x"),
            PlanStep(id="s1", agent="a", task="y"),
        ])


def test_rejects_unknown_dependency() -> None:
    with pytest.raises(Exception, match="unknown steps"):
        _plan([PlanStep(id="s1", agent="a", task="x", depends_on=["s9"])])


def test_rejects_cycle() -> None:
    with pytest.raises(Exception, match="cycle"):
        _plan([
            PlanStep(id="s1", agent="a", task="x", depends_on=["s2"]),
            PlanStep(id="s2", agent="a", task="y", depends_on=["s1"]),
        ])


def test_rejects_more_than_six_steps() -> None:
    steps = [PlanStep(id=f"s{i}", agent="a", task="x") for i in range(1, 8)]
    with pytest.raises(Exception):
        _plan(steps)


def test_rejects_more_than_eight_tools_on_a_step() -> None:
    tools = [f"tool_{i}" for i in range(9)]
    with pytest.raises(Exception):
        PlanStep(id="s1", agent="a", task="x", tools=tools)


def test_parallel_root_steps_valid() -> None:
    plan = _plan([
        PlanStep(id="s1", agent="jira-analyst", task="Fetch PLAT health", depends_on=[]),
        PlanStep(id="s2", agent="jira-analyst", task="Fetch NOVA health", depends_on=[]),
        PlanStep(id="s3", agent="data-scientist", task="Compare",
                 depends_on=["s1", "s2"]),
    ])
    roots = [step.id for step in plan.steps if not step.depends_on]
    assert roots == ["s1", "s2"]
    ordered = [s.id for s in plan.in_order()]
    assert ordered.index("s3") > ordered.index("s1")
    assert ordered.index("s3") > ordered.index("s2")
