"""Typed event contract for the SSE trace stream."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    ROUTER_DECISION = "router_decision"
    PLAN_CREATED = "plan_created"
    AGENT_SPAWNED = "agent_spawned"
    NODE_STARTED = "node_started"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    TOKEN_USAGE = "token_usage"
    LLM_CALL_STARTED = "llm_call_started"
    EMBEDDING_USAGE = "embedding_usage"
    NODE_FINISHED = "node_finished"
    APPROVAL_REQUIRED = "approval_required"
    CLARIFY_REQUIRED = "clarify_required"
    ANSWER_DELTA = "answer_delta"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    CONTEXT_SNAPSHOT = "context_snapshot"


class BaseEvent(BaseModel):
    type: EventType
    run_id: str
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    seq: int = 0  # assigned by the event bus at emit time


class RunStartedEvent(BaseEvent):
    goal: str


class RouterDecisionEvent(BaseEvent):
    intent: str  # simple | complex | clarify
    path: str  # fast | plan
    reason: str = ""
    temporal_sensitivity: bool = False


class PlanCreatedEvent(BaseEvent):
    plan: dict[str, Any]
    hint_used: bool = False


class AgentSpawnedEvent(BaseEvent):
    step_id: str
    agent: str
    tools: list[str] = []


class NodeStartedEvent(BaseEvent):
    node: str


class ToolCallStartedEvent(BaseEvent):
    step_id: str
    tool: str
    args_digest: dict[str, Any] = {}
    mode: str = ""  # serial | parallel


class ToolCallFinishedEvent(BaseEvent):
    step_id: str
    tool: str
    status: str  # ok | error | timeout
    ms: int
    digest_preview: str = ""
    result_id: str | None = None
    mode: str = ""  # serial | parallel


class TokenUsageEvent(BaseEvent):
    node: str
    prompt: int
    completion: int
    reasoning: int = 0
    visible_completion: int = 0
    cost_usd: float
    model: str = ""
    ms: int | None = None
    kind: str = "llm"
    reasoning_effort: str = ""
    input_preview: str = ""
    output_preview: str = ""


class LlmCallStartedEvent(BaseEvent):
    node: str
    label: str = ""
    input_preview: str = ""


class EmbeddingUsageEvent(BaseEvent):
    node: str
    model: str
    provider: str
    tokens: int
    cost_usd: float
    ms: int
    input_type: str = ""
    cached: bool = False


class NodeFinishedEvent(BaseEvent):
    node: str
    summary: str = ""


class ApprovalRequiredEvent(BaseEvent):
    pending_tool: str
    reason: str


class ClarifyRequiredEvent(BaseEvent):
    question: str
    step_id: str = ""
    reason: str = "missing_scope"


class AnswerDeltaEvent(BaseEvent):
    text: str
    done: bool = False


class RunFinishedEvent(BaseEvent):
    result: str
    totals: dict[str, Any] = {}


class RunFailedEvent(BaseEvent):
    error: str  # user-safe message only


class ContextSnapshotEvent(BaseEvent):
    node: str
    sections: list[dict[str, str]] = []


EVENT_MODEL_BY_TYPE: dict[EventType, type[BaseEvent]] = {
    EventType.RUN_STARTED: RunStartedEvent,
    EventType.ROUTER_DECISION: RouterDecisionEvent,
    EventType.PLAN_CREATED: PlanCreatedEvent,
    EventType.AGENT_SPAWNED: AgentSpawnedEvent,
    EventType.NODE_STARTED: NodeStartedEvent,
    EventType.TOOL_CALL_STARTED: ToolCallStartedEvent,
    EventType.TOOL_CALL_FINISHED: ToolCallFinishedEvent,
    EventType.TOKEN_USAGE: TokenUsageEvent,
    EventType.LLM_CALL_STARTED: LlmCallStartedEvent,
    EventType.EMBEDDING_USAGE: EmbeddingUsageEvent,
    EventType.NODE_FINISHED: NodeFinishedEvent,
    EventType.APPROVAL_REQUIRED: ApprovalRequiredEvent,
    EventType.CLARIFY_REQUIRED: ClarifyRequiredEvent,
    EventType.ANSWER_DELTA: AnswerDeltaEvent,
    EventType.RUN_FINISHED: RunFinishedEvent,
    EventType.RUN_FAILED: RunFailedEvent,
    EventType.CONTEXT_SNAPSHOT: ContextSnapshotEvent,
}


def make_event(event_type: EventType, run_id: str, /, **fields: Any) -> BaseEvent:
    """Typed event factory — validates fields against the event model."""
    model = EVENT_MODEL_BY_TYPE[event_type]
    return model(type=event_type, run_id=run_id, **fields)


def serialize_event(event: BaseEvent) -> dict[str, Any]:
    """Wire format for SSE `data:` payloads."""
    return event.model_dump(mode="json", exclude_none=True)
