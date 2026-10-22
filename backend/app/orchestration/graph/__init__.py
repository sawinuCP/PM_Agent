"""LangGraph runtime — builder, worker ReAct loop, and synthesis."""

from __future__ import annotations

from typing import Any

from app.orchestration.graph.synthesis import compose_synthesis_prompt
from app.orchestration.graph.worker import emit_usage, execute_worker_step, max_tool_rounds_for

__all__ = [
    "GraphBundle",
    "build_graph",
    "compose_synthesis_prompt",
    "emit_usage",
    "execute_worker_step",
    "max_tool_rounds_for",
]


def __getattr__(name: str) -> Any:
    if name in {"GraphBundle", "build_graph"}:
        from app.orchestration.graph.builder import GraphBundle, build_graph

        return GraphBundle if name == "GraphBundle" else build_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
