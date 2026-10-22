"""E2E smoke scenario profiles — goals and per-scenario pass criteria."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenarioContext:
    """Outcome of one chat turn (or the final turn in a multi-turn profile)."""

    run_id: str
    session_id: str
    thread_id: str
    goal: str
    run: Any
    traces: list[Any]
    all_runs: list[Any] = field(default_factory=list)
    all_trace_sets: list[list[Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


ValidateFn = Callable[[ScenarioContext], tuple[bool, list[str]]]
MidRunFn = Callable[[ScenarioContext, Any, Any], Awaitable[None]]


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    description: str
    goals: tuple[str, ...]
    timeout_s: float = 180.0
    mid_run: MidRunFn | None = None
    validate: ValidateFn | None = None


def _trace_types(traces: list[Any]) -> list[str]:
    return [t.event_type for t in traces]


def _has_event(traces: list[Any], event_type: str) -> bool:
    return any(t.event_type == event_type for t in traces)


def _result_text(ctx: ScenarioContext) -> str:
    return (ctx.run.result_summary or "").lower()


def _validate_completed(ctx: ScenarioContext, failures: list[str]) -> None:
    if ctx.run.status != "completed":
        failures.append(f"expected status=completed, got {ctx.run.status!r}")


def _validate_run_finished(ctx: ScenarioContext, failures: list[str]) -> None:
    if not _has_event(ctx.traces, "run_finished"):
        failures.append("missing run_finished trace event")


def validate_velocity(ctx: ScenarioContext) -> tuple[bool, list[str]]:
    failures: list[str] = []
    _validate_completed(ctx, failures)
    _validate_run_finished(ctx, failures)
    for required in ("plan_created", "agent_spawned"):
        if not _has_event(ctx.traces, required):
            failures.append(f"missing {required}")
    tool_ok = any(
        t.event_type == "tool_call_finished" and t.payload.get("status") == "ok"
        for t in ctx.traces
    )
    if not tool_ok:
        failures.append("no successful tool_call_finished")
    text = _result_text(ctx)
    if not any(c.isdigit() for c in text):
        failures.append("result should include a numeric forecast")
    if not any(w in text for w in ("sprint", "velocity", "point", "forecast", "predict")):
        failures.append("result should mention sprint/velocity/forecast")
    return not failures, failures


def validate_bugs(ctx: ScenarioContext) -> tuple[bool, list[str]]:
    failures: list[str] = []
    _validate_completed(ctx, failures)
    _validate_run_finished(ctx, failures)
    for required in ("plan_created", "agent_spawned"):
        if not _has_event(ctx.traces, required):
            failures.append(f"missing {required}")
    if not _has_event(ctx.traces, "tool_call_finished"):
        failures.append("missing tool_call_finished")
    text = _result_text(ctx)
    if "bug" not in text and "sprint" not in text:
        failures.append("result should discuss bugs or sprints")
    return not failures, failures


def validate_fast(ctx: ScenarioContext) -> tuple[bool, list[str]]:
    failures: list[str] = []
    _validate_completed(ctx, failures)
    _validate_run_finished(ctx, failures)
    router = [t for t in ctx.traces if t.event_type == "router_decision"]
    if not router:
        failures.append("missing router_decision")
    elif router[0].payload.get("path") != "fast":
        failures.append(f"expected router path=fast, got {router[0].payload.get('path')!r}")
    if _has_event(ctx.traces, "plan_created"):
        failures.append("fast path should not emit plan_created")
    if _has_event(ctx.traces, "agent_spawned"):
        failures.append("fast path should not spawn agents")
    text = _result_text(ctx)
    if len(text) < 20:
        failures.append("expected a conversational answer")
    return not failures, failures


async def mid_run_approve_hitl(ctx: ScenarioContext, app_db: Any, platform: Any) -> None:
    """Resume a run parked on sensitive-tool approval (supports multiple gates)."""
    active = platform.executor._active.get(ctx.run_id)
    if active is not None and active.bundle is not None and active.bundle.pending_tool:
        if platform.executor.resolve_approval(ctx.run_id, True):
            await app_db.update_run_record(ctx.run_id, status="running")
            ctx.extras["hitl_resolved"] = True
        return
    run = await app_db.get_run_record(ctx.run_id)
    if run is not None and run.status == "awaiting_approval":
        if platform.executor.resolve_approval(ctx.run_id, True):
            await app_db.update_run_record(ctx.run_id, status="running")
            ctx.extras["hitl_resolved"] = True


def validate_hitl(ctx: ScenarioContext) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not ctx.extras.get("hitl_resolved"):
        failures.append("approval was never resolved")
    _validate_completed(ctx, failures)
    _validate_run_finished(ctx, failures)
    if not _has_event(ctx.traces, "approval_required"):
        failures.append("missing approval_required event")
    if not _has_event(ctx.traces, "plan_created"):
        failures.append("missing plan_created")
    create_calls = [
        t for t in ctx.traces
        if t.event_type == "tool_call_started"
        and str(t.payload.get("tool", "")).startswith("jira_create")
    ]
    if not create_calls:
        failures.append("expected a jira_create_* tool call")
    return not failures, failures


def validate_episodic_repeat(ctx: ScenarioContext) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if len(ctx.all_runs) < 2:
        failures.append("episodic_repeat requires two completed runs")
        return False, failures
    first, second = ctx.all_runs[0], ctx.all_runs[1]
    if first.status != "completed" or second.status != "completed":
        failures.append("both turns must complete")
    second_traces = ctx.all_trace_sets[1] if len(ctx.all_trace_sets) > 1 else ctx.traces
    plan_events = [t for t in second_traces if t.event_type == "plan_created"]
    if not plan_events:
        failures.append("turn 2 missing plan_created")
    elif not plan_events[0].payload.get("hint_used"):
        failures.append("turn 2 plan_created.hint_used should be true")
    return not failures, failures


_VELOCITY_GOAL = (
    "Predict the velocity for our next sprint based on previous Jira sprints"
)

SCENARIO_PROFILES: dict[str, ScenarioProfile] = {
    "velocity": ScenarioProfile(
        name="velocity",
        description="Assignment example: Jira fetch -> sandbox forecast -> synthesis",
        goals=(_VELOCITY_GOAL,),
        validate=validate_velocity,
    ),
    "bugs": ScenarioProfile(
        name="bugs",
        description="Bug counts across sprints with trend analysis",
        goals=(
            "Compare bug counts across the last 3 sprints and analyze their trend",
        ),
        validate=validate_bugs,
    ),
    "fast": ScenarioProfile(
        name="fast",
        description="Router fast path - conversational, no planner graph",
        goals=("Hi, what can you do?",),
        timeout_s=60.0,
        validate=validate_fast,
    ),
    "hitl": ScenarioProfile(
        name="hitl",
        description="Sensitive Jira write parks run until human approval",
        goals=(
            "Create a Jira issue in project PLAT with summary Track next sprint commitments",
        ),
        mid_run=mid_run_approve_hitl,
        validate=validate_hitl,
    ),
    "episodic_repeat": ScenarioProfile(
        name="episodic_repeat",
        description="Second identical goal reuses episodic plan hint",
        goals=(_VELOCITY_GOAL, _VELOCITY_GOAL),
        validate=validate_episodic_repeat,
    ),
}

PROFILE_NAMES = tuple(SCENARIO_PROFILES.keys()) + ("all",)


def get_profile(name: str) -> ScenarioProfile:
    key = name.strip().lower()
    if key not in SCENARIO_PROFILES:
        raise KeyError(key)
    return SCENARIO_PROFILES[key]
