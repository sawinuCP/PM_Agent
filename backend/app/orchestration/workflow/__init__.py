"""Run lifecycle — pipeline turns, executor, and clarify parking."""

from __future__ import annotations

from typing import Any

from app.orchestration.workflow.clarify import (
    enrich_goal_with_clarification,
    find_pending_clarify_run,
    park_clarify,
    persist_assistant_message,
)
from app.orchestration.workflow.pipeline import fast_path_answer, run_turn

__all__ = [
    "ActiveRun",
    "Executor",
    "drain_post_runs",
    "enrich_goal_with_clarification",
    "fast_path_answer",
    "find_pending_clarify_run",
    "park_clarify",
    "persist_assistant_message",
    "run_turn",
]


def __getattr__(name: str) -> Any:
    if name in {"ActiveRun", "Executor", "drain_post_runs"}:
        from app.orchestration.workflow import executor as _executor

        return getattr(_executor, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
