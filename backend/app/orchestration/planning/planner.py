"""Planner — live LLM → validated Plan (production path)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.agents.registry import AgentRegistry
from app.ai.prompts import PLANNER_SYSTEM
from app.ai.provider import LLMProvider
from app.core.errors import AIStructuredOutputError, PlanValidationError
from app.core.llm_budgets import conversation_tail, planner_conversation_chars
from app.mcp_integration.manager import MCPManager
from app.memory.runtime import mark_plan_hint_usage
from app.models.plan import Plan
from app.orchestration.planning.plan_deps import normalize_join_dependencies
from app.orchestration.planning.plan_tools import sanitize_plan, validate_plan

logger = logging.getLogger(__name__)


@dataclass
class PlanningContext:
    thread_id: str
    goal: str
    conversation: str = ""
    conversation_memory: dict[str, Any] | None = None
    temporal_sensitivity: bool = False


def planner_catalog(registry: AgentRegistry, manager: MCPManager) -> dict[str, Any]:
    """Agent capability menu — planner picks roles and tasks, not tool names."""
    live = {
        item["name"]
        for catalog in manager.tool_catalog().values()
        for item in catalog
    }
    agents = []
    for entry in registry.capability_catalog():
        if entry.name == "conversational":
            continue
        live_tools = [name for name in entry.tools if name in live]
        agents.append({
            "name": entry.name,
            "role": entry.description,
            "capabilities": entry.capabilities,
            "tool_count": len(live_tools),
        })
    return {"agents": agents}


async def create_plan(context: PlanningContext, provider: LLMProvider,
                      registry: AgentRegistry, manager: MCPManager,
                      hints: list[dict[str, Any]] | None = None) -> tuple[Plan | None, bool]:
    """Returns (plan, planner_llm_used). Always uses the planner LLM."""
    allowed = {entry.name: list(entry.tools) for entry in registry.capability_catalog()}
    catalog_tools = {
        item["name"]
        for catalog in manager.tool_catalog().values()
        for item in catalog
    }

    payload: dict[str, Any] = {
        **planner_catalog(registry, manager),
        "goal": context.goal,
        "thread_id": context.thread_id,
        "temporal_sensitivity": context.temporal_sensitivity,
    }
    if hints:
        payload["historical_hints"] = hints
    if context.conversation_memory:
        payload["conversation_memory"] = context.conversation_memory
    elif context.conversation:
        payload["conversation_context"] = conversation_tail(
            context.conversation, planner_conversation_chars())

    user_prompt = (
        f"GOAL: {context.goal}\nReply with the Plan JSON object only.\n\nCONTEXT: "
        + json.dumps(payload, default=str)
    )
    try:
        plan, _usage = await provider.generate_structured(
            PLANNER_SYSTEM, user_prompt, Plan, tier="planner", repairs=0)
        plan = mark_plan_hint_usage(plan, hints)
        if plan.clarify_question and plan.clarify_question.strip():
            return plan, True
        sanitize_plan(plan, allowed_by_agent=allowed, catalog_tools=catalog_tools)
        normalize_join_dependencies(plan)
        validate_plan(plan, allowed_by_agent=allowed, catalog_tools=catalog_tools)
        logger.info("plan created: %d step(s) agents=%s hint=%s",
                    len(plan.steps), [step.agent for step in plan.steps],
                    plan.uses_episodic_hint)
        return plan, True
    except (AIStructuredOutputError, PlanValidationError) as exc:
        logger.warning("planner failed validation: %s", exc)
        return None, True
    except Exception as exc:  # noqa: BLE001
        logger.warning("planner LLM error: %s", exc)
        return None, True
