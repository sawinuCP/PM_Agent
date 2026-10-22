"""HTTP API request and response DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str = Field(default="New session", max_length=200)


class SessionOut(BaseModel):
    id: str
    title: str
    thread_id: str
    status: str
    message_count: int
    total_tokens: int
    total_cost_usd: float
    created_at: str
    updated_at: str


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=8000)


class ChatAccepted(BaseModel):
    run_id: str
    thread_id: str
    status: str


class ApprovalRequest(BaseModel):
    approved: bool
    reviewer: str = Field(default="demo", max_length=80)
    reason: str = Field(default="", max_length=500)


class ClarifyRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(default="demo", max_length=80)


class HealthOut(BaseModel):
    status: str
    version: str = "1.0.0"


class InferenceOut(BaseModel):
    provider: str
    model: str
    live: bool
    embeddings: str = "hash"


class ReadinessOut(BaseModel):
    status: str
    database: str
    mcp_servers: dict[str, str] = Field(default_factory=dict)
    inference: InferenceOut | None = None


class TelemetryBucket(BaseModel):
    bucket: str
    traces: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    avg_latency_ms: float | None = None


class TelemetryRecent(BaseModel):
    id: str
    status: str
    goal: str
    tokens: int = 0
    cost_usd: float = 0.0
    started_at: str
    duration_ms: float | None = None


class TelemetryTotals(BaseModel):
    traces: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    avg_latency_ms: float | None = None
    success_rate: float | None = None


class TelemetryOut(BaseModel):
    range: str
    from_ts: str
    to_ts: str
    totals: TelemetryTotals
    series: list[TelemetryBucket] = Field(default_factory=list)
    by_status: dict[str, int] = Field(default_factory=dict)
    recent: list[TelemetryRecent] = Field(default_factory=list)


class RunQualityOut(BaseModel):
    run_id: str
    status: str
    path: str | None = None
    intent: str | None = None
    planner_used: bool = False
    plan_steps: int = 0
    agents: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    tool_errors: int = 0
    clarify_events: int = 0
    approval_events: int = 0
    reasoning_tokens: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: float | None = None
    nodes: list[str] = Field(default_factory=list)
    hint_used: bool = False
    flags: list[str] = Field(default_factory=list)
