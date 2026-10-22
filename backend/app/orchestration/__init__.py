"""Multi-agent orchestration platform.

Subpackages
-----------
workflow   Run lifecycle (pipeline, executor, clarify)
routing    Router fast-path vs plan-path classification
planning   Planner LLM, plan validation, DAG normalization
graph      LangGraph builder, worker ReAct loop, synthesis
hitl       Human approval and clarify interrupt metadata
tooling    MCP tool chains, harvest, retry, validation
coverage   Entity/scope evidence gates before worker finish
utils      Hop parsing and schema field helpers

Root
----
state      LangGraph orchestrator state TypedDict
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Executor",
    "drain_post_runs",
    "run_turn",
]


def __getattr__(name: str) -> Any:
    if name == "Executor":
        from app.orchestration.workflow.executor import Executor

        return Executor
    if name == "drain_post_runs":
        from app.orchestration.workflow.executor import drain_post_runs

        return drain_post_runs
    if name == "run_turn":
        from app.orchestration.workflow.pipeline import run_turn

        return run_turn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
