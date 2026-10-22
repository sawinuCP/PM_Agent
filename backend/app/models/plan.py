"""Planning, routing, worker, agent template, and analytics contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    id: str = Field(pattern=r"^s\d+$", description="Step id, e.g. 's1'")
    agent: str = Field(description="Agent template name — must exist in the registry")
    task: str = Field(min_length=1, description="Natural-language task for the agent")
    tools: list[str] = Field(
        default_factory=list, max_length=8,
        description="Deprecated — always cleared by sanitize_plan(); workers choose tools",
    )
    depends_on: list[str] = Field(default_factory=list, description="Step ids this step waits on (DAG edges)")
    tool_mode: Literal["auto", "serial", "parallel"] = Field(
        default="auto",
        description="Worker execution hint (auto); not set by planner in V2",
    )
    arg_bindings: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Deprecated — cleared by sanitize_plan(); worker picks args from MCP schemas",
    )
    arg_resolution: list[str] = Field(
        default_factory=list, max_length=8,
        description="Deprecated — cleared by sanitize_plan()",
    )


class Plan(BaseModel):
    thread_id: str
    goal: str
    steps: list[PlanStep] = Field(default_factory=list, max_length=6)
    synthesis: str = Field(default="", description="Instruction for the aggregator node")
    uses_episodic_hint: bool = False
    reasoning: list[str] = Field(
        default_factory=list, max_length=12,
        description="Ordered chain-of-thought explaining agents, tools, and execution path",
    )
    clarify_question: str | None = Field(
        default=None,
        description="When set, ask the user this question instead of executing steps",
    )

    @model_validator(mode="after")
    def _validate_dag(self) -> Plan:
        if self.clarify_question and self.clarify_question.strip():
            return self
        if len(self.steps) < 1:
            raise ValueError("plan requires at least one step when clarify_question is absent")
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("duplicate step ids in plan")
        for step in self.steps:
            unknown = set(step.depends_on) - ids
            if unknown:
                raise ValueError(f"step {step.id} depends on unknown steps: {sorted(unknown)}")
        if self._has_cycle():
            raise ValueError("plan dependency graph contains a cycle")
        return self

    def _has_cycle(self) -> bool:
        graph = {s.id: set(s.depends_on) for s in self.steps}
        state: dict[str, int] = {}

        def visit(node: str) -> bool:
            if state.get(node) == 1:
                return True
            if state.get(node) == 2:
                return False
            state[node] = 1
            if any(dep in graph and visit(dep) for dep in graph[node]):
                return True
            state[node] = 2
            return False

        return any(node in graph and visit(node) for node in graph)

    def in_order(self) -> list[PlanStep]:
        """Steps in dependency order (stable; preserves planner ordering)."""
        done: set[str] = set()
        ordered: list[PlanStep] = []
        remaining = list(self.steps)
        while remaining:
            ready = [s for s in remaining if set(s.depends_on) <= done]
            if not ready:  # defensive; cycle detection runs in the validator
                raise ValueError("plan dependency graph contains a cycle")
            for step in ready:
                ordered.append(step)
                done.add(step.id)
            remaining = [s for s in remaining if s not in ready]
        return ordered


class PermissionTier(str, Enum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"


class AgentModelConfig(BaseModel):
    provider: str = "inherit"  # inherit | mock | openai
    tier: str = "standard"  # cheap | standard


class AgentPermissions(BaseModel):
    tier: PermissionTier = PermissionTier.STANDARD
    requires_approval_tools: list[str] = Field(default_factory=list)


class AgentMemoryConfig(BaseModel):
    working_token_budget: int = 16000
    semantic_injection: bool = True


class AgentTemplate(BaseModel):
    """One YAML file = one spawnable agent type. Zero code to add an agent."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: int = 1
    description: str
    capabilities: list[str] = Field(default_factory=list)
    system_prompt: str = Field(min_length=1)
    allowed_mcp_servers: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    model: AgentModelConfig = Field(default_factory=AgentModelConfig)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)


class CapabilityCatalogEntry(BaseModel):
    """Compact agent descriptor surfaced to the Planner."""

    name: str
    description: str
    capabilities: list[str]
    tools: list[str]
    servers: list[str]
    permission_tier: str


class RouterOutput(BaseModel):
    """Router classifier output (cheap tier, structured)."""

    intent: str = Field(pattern=r"^(simple|complex|clarify)$")
    direct_answer: str | None = None
    temporal_sensitivity: bool = False


class WorkerDecision(BaseModel):
    """One round of the agentic tool loop: call a tool or finish."""

    action: str = Field(pattern=r"^(call_tool|finish)$")
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None

    @model_validator(mode="after")
    def _bound_tool_args(self) -> WorkerDecision:
        if self.action == "call_tool" and not self.tool:
            raise ValueError("call_tool requires a tool name")
        if self.args is None or not isinstance(self.args, dict):
            raise ValueError("tool arguments must be a JSON object")
        if len(str(self.args)) > 12_000:
            raise ValueError("tool arguments exceed the safety cap")
        return self


class SynthesisOutput(BaseModel):
    """Aggregator must return structured JSON; `answer` is shown to the user."""

    answer: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


class AnalysisPoint(BaseModel):
    """One observed window in a sprint/issue series (sandbox + in-process)."""

    sprint_id: str = ""
    name: str = ""
    value: float
    metric: str = "value"
    committed: float | None = None


class AnalysisResult(BaseModel):
    """Validated analytics payload returned by run_python / analyze_goal.

    Machine fields (metric, forecast, direction) stay in the object so the
    sandbox can be checked. User-facing text lives in headline + briefing.
    """

    method: str
    kind: str = Field(pattern=r"^(velocity|bugs|volume|none)$")
    headline: str = ""
    briefing: str = ""
    series: list[AnalysisPoint] = Field(default_factory=list)
    forecast: float | None = None
    window: int = 0
    direction: str = Field(pattern=r"^(rising|falling|flat|n/a)$")
    metric: str = "value"
    slope: float = 0.0
    mean: float = 0.0
    note: str = ""
    asked: str = ""
