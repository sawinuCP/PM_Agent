"""Unit tests for e2e smoke scenario validators (no live MCP)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from e2e_scenarios import (  # noqa: E402
    ScenarioContext,
    validate_episodic_repeat,
    validate_fast,
    validate_velocity,
)


def _trace(event_type: str, **payload: object) -> SimpleNamespace:
    return SimpleNamespace(event_type=event_type, payload=payload)


def test_validate_fast_rejects_planner_path() -> None:
    ctx = ScenarioContext(
        run_id="r1",
        session_id="s1",
        thread_id="t1",
        goal="hi",
        run=SimpleNamespace(status="completed", result_summary="Hello from Azend — I help with Jira and analytics."),
        traces=[
            _trace("router_decision", path="fast"),
            _trace("run_finished"),
        ],
    )
    ok, failures = validate_fast(ctx)
    assert ok
    assert failures == []


def test_validate_velocity_requires_tools() -> None:
    ctx = ScenarioContext(
        run_id="r1",
        session_id="s1",
        thread_id="t1",
        goal="predict velocity",
        run=SimpleNamespace(status="completed", result_summary="Sprint 5 forecast: 38 points"),
        traces=[
            _trace("plan_created"),
            _trace("agent_spawned"),
            _trace("tool_call_finished", status="ok"),
            _trace("run_finished"),
        ],
    )
    ok, _ = validate_velocity(ctx)
    assert ok


def test_validate_episodic_repeat_checks_hint_used() -> None:
    first = SimpleNamespace(status="completed", result_summary="done")
    second = SimpleNamespace(status="completed", result_summary="done again")
    ctx = ScenarioContext(
        run_id="r2",
        session_id="s1",
        thread_id="t1",
        goal="predict velocity",
        run=second,
        traces=[_trace("plan_created", hint_used=True), _trace("run_finished")],
        all_runs=[first, second],
        all_trace_sets=[
            [_trace("plan_created", hint_used=False), _trace("run_finished")],
            [_trace("plan_created", hint_used=True), _trace("run_finished")],
        ],
    )
    ok, failures = validate_episodic_repeat(ctx)
    assert ok
    assert failures == []
