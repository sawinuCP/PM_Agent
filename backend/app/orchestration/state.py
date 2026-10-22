"""OrchestratorState — LangGraph state contract with parallel-safe reducers.

Plain TypedDict with per-key last-value merging; worker nodes return partial
updates. Usage/cost tracking lives in the executor, not in graph state.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer: merge per-step maps instead of overwriting (parallel-safe)."""
    merged: dict[str, Any] = dict(left or {})
    merged.update(right or {})
    return merged


class OrchestratorState(TypedDict, total=False):
    run_id: str
    thread_id: str
    goal: str
    conversation: str
    session_context: dict[str, Any]
    plan: dict[str, Any]  # serialized Plan
    clarify_required: str
    step_outputs: Annotated[dict[str, str], merge_dicts]
    tool_result_refs: Annotated[dict[str, list[str]], merge_dicts]
    final_answer: str
    error: str | None
