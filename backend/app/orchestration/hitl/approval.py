"""Human-in-the-loop gates — clarify parking and sensitive-tool approval."""

from __future__ import annotations

from typing import Any, Protocol

from app.models.plan import AgentTemplate


class MCPSensitivity(Protocol):
    def is_tool_sensitive(self, tool_name: str) -> bool: ...


def approval_required(
    tool_name: str,
    *,
    agent: AgentTemplate,
    manager: MCPSensitivity,
) -> bool:
    """True when platform policy requires human approval before executing."""
    if tool_name in agent.permissions.requires_approval_tools:
        return True
    return manager.is_tool_sensitive(tool_name)


def clarify_interrupt_metadata(
    *,
    goal: str,
    question: str,
    conversation: str,
    replan_count: int = 0,
) -> dict[str, Any]:
    """Persisted shape for a paused clarify run (_interrupt block)."""
    return {
        "kind": "clarify",
        "question": question.strip(),
        "goal": goal,
        "conversation": conversation,
        "replan_count": replan_count,
    }
