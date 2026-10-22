"""Agent factory — spawn runtime agents from plan steps and YAML templates.

Turns a PlanStep + YAML template into a runnable agent: loads the template,
injects provision-time semantic memories, resolves the tier-aware LLM client,
requests the permission-filtered MCP toolset, and wraps everything in a worker
callable with per-agent token budgets. Agents exist only inside a run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents.registry import AgentRegistry
from app.agents.worker import trim_system_prompt
from app.ai.prompts import CONTEXT_BLOCK, HISTORICAL_HINT_WRAPPER
from app.ai.provider import LLMProvider
from app.mcp_integration.digest import decode_upstream
from app.mcp_integration.manager import MCPManager
from app.memory.runtime import semantic_notes_for_agent
from app.models.plan import AgentTemplate, PlanStep
from app.observability.events import EventType, make_event

logger = logging.getLogger(__name__)


@dataclass
class SpawnedAgent:
    """A runtime agent instance — lives for exactly one run."""

    template: AgentTemplate
    step: PlanStep
    provider: LLMProvider
    tools: list[Any] = field(default_factory=list)
    semantic_notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.template.name


class AgentFactory:
    def __init__(self, registry: AgentRegistry, manager: MCPManager,
                 provider: LLMProvider) -> None:
        self._registry = registry
        self._manager = manager
        self._provider = provider

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    async def spawn(self, step: PlanStep, *, run_id: str, thread_id: str,
                    goal: str, emit=None) -> SpawnedAgent:
        """Spawn sequence: template → memories → LLM → bound tools → worker."""
        template = self._registry.get(step.agent)
        semantic_notes = await self._semantic_notes(template, goal)
        tools = await self._manager.bind_toolset(
            template, step_id=step.id, run_id=run_id, emit=emit,
            requested_tools=None)
        agent = SpawnedAgent(
            template=template,
            step=step,
            provider=self._provider,
            tools=tools,
            semantic_notes=semantic_notes,
        )
        if emit is not None:
            await emit(make_event(EventType.AGENT_SPAWNED, run_id, step_id=step.id,
                                  agent=template.name,
                                  tools=[t.name for t in tools]))
        logger.info("spawned '%s' for step %s with %d tools", template.name, step.id, len(tools))
        return agent

    async def _semantic_notes(self, template: AgentTemplate, goal: str) -> list[str]:
        """L4 semantic facts injected at provision time."""
        role = (template.description or template.name).strip()
        return await semantic_notes_for_agent(role, goal)

    def build_system_prompt(self, agent: SpawnedAgent,
                            upstream_digests: dict[str, str]) -> str:
        """YAML role prompt + worker envelope + upstream context + L4 hints."""
        protected: list[tuple[str, str]] = []
        if upstream_digests:
            rendered = json.dumps(
                {"type": "upstream", "steps": decode_upstream(upstream_digests)},
                default=str,
            )
            if len(rendered) > 3200:
                rendered = rendered[:3200] + '..."[upstream truncated]"'
            protected.append((
                "upstream",
                CONTEXT_BLOCK.format(upstream_digests=rendered),
            ))
        shrinkable: list[tuple[str, str]] = []
        if agent.semantic_notes:
            shrinkable.append((
                "semantic",
                HISTORICAL_HINT_WRAPPER.format(
                    hint_text=json.dumps(
                        {"type": "semantic_facts", "facts": agent.semantic_notes},
                        ensure_ascii=False,
                    ),
                ),
            ))

        role = (agent.template.system_prompt or "").strip()
        budget = agent.template.memory.working_token_budget
        return trim_system_prompt(
            budget,
            role=role,
            agent_name=agent.name,
            step_id=agent.step.id,
            task=agent.step.task,
            protected_blocks=protected,
            shrinkable_blocks=shrinkable,
        )
