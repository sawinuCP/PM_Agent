/**
 * Mirrored SSE event contract (backend: app/observability/events.py §8.2).
 * Hand-maintained 1:1 with EventType + the per-event Pydantic models so the
 * React client and the FastAPI backend cannot drift on event shapes.
 */

export enum EventType {
  RunStarted = "run_started",
  RouterDecision = "router_decision",
  PlanCreated = "plan_created",
  AgentSpawned = "agent_spawned",
  NodeStarted = "node_started",
  ToolCallStarted = "tool_call_started",
  ToolCallFinished = "tool_call_finished",
  TokenUsage = "token_usage",
  LlmCallStarted = "llm_call_started",
  EmbeddingUsage = "embedding_usage",
  NodeFinished = "node_finished",
  ApprovalRequired = "approval_required",
  ClarifyRequired = "clarify_required",
  AnswerDelta = "answer_delta",
  RunFinished = "run_finished",
  RunFailed = "run_failed",
  ContextSnapshot = "context_snapshot",
}

export interface PlanStep {
  id: string;
  agent: string;
  task: string;
  tools: string[];
  depends_on: string[];
  tool_mode?: "auto" | "serial" | "parallel";
}

/** Clarify-parked runs may persist plan metadata without a steps array. */
export function planSteps(steps: PlanStep[] | null | undefined): PlanStep[] {
  return Array.isArray(steps) ? steps : [];
}

export interface BaseEvent {
  run_id: string;
  ts: string;
  seq: number;
}

export interface RunStartedEvent extends BaseEvent {
  type: EventType.RunStarted;
  goal: string;
}
export interface RouterDecisionEvent extends BaseEvent {
  type: EventType.RouterDecision;
  intent: "simple" | "complex" | "clarify";
  path: "fast" | "plan" | "context_recall" | "context_clarify" | string;
  reason: string;
  temporal_sensitivity: boolean;
}
export interface PlanCreatedEvent extends BaseEvent {
  type: EventType.PlanCreated;
  plan: {
    steps: PlanStep[];
    synthesis: string;
    uses_episodic_hint: boolean;
    reasoning?: string[];
  };
  hint_used: boolean;
}
export interface AgentSpawnedEvent extends BaseEvent {
  type: EventType.AgentSpawned;
  step_id: string;
  agent: string;
  tools: string[];
}
export interface NodeStartedEvent extends BaseEvent {
  type: EventType.NodeStarted;
  node: string;
}
export interface ToolCallStartedEvent extends BaseEvent {
  type: EventType.ToolCallStarted;
  step_id: string;
  tool: string;
  args_digest: Record<string, unknown>;
  mode?: "serial" | "parallel" | "";
}
export interface ToolCallFinishedEvent extends BaseEvent {
  type: EventType.ToolCallFinished;
  step_id: string;
  tool: string;
  status: "ok" | "error" | "timeout";
  ms: number;
  digest_preview: string;
  result_id: string | null;
  mode?: "serial" | "parallel" | "";
}
export interface TokenUsageEvent extends BaseEvent {
  type: EventType.TokenUsage;
  node: string;
  prompt: number;
  completion: number;
  reasoning?: number;
  visible_completion?: number;
  cost_usd: number;
  model?: string;
  ms?: number | null;
  kind?: string;
  reasoning_effort?: string;
  input_preview?: string;
  output_preview?: string;
}
export interface LlmCallStartedEvent extends BaseEvent {
  type: EventType.LlmCallStarted;
  node: string;
  label: string;
  input_preview?: string;
}
export interface EmbeddingUsageEvent extends BaseEvent {
  type: EventType.EmbeddingUsage;
  node: string;
  model: string;
  provider: string;
  tokens: number;
  cost_usd: number;
  ms: number;
  input_type: string;
  cached: boolean;
}
export interface NodeFinishedEvent extends BaseEvent {
  type: EventType.NodeFinished;
  node: string;
  summary: string;
}
export interface ApprovalRequiredEvent extends BaseEvent {
  type: EventType.ApprovalRequired;
  pending_tool: string;
  reason: string;
}
export interface ClarifyRequiredEvent extends BaseEvent {
  type: EventType.ClarifyRequired;
  question: string;
  step_id?: string;
  reason?: string;
}
export interface AnswerDeltaEvent extends BaseEvent {
  type: EventType.AnswerDelta;
  text: string;
  done: boolean;
}
export interface RunFinishedEvent extends BaseEvent {
  type: EventType.RunFinished;
  result: string;
  totals: Record<string, unknown>;
}
export interface RunFailedEvent extends BaseEvent {
  type: EventType.RunFailed;
  error: string;
}
export interface ContextSection {
  key: string;
  title: string;
  content: string;
  format: "text" | "json";
}
export interface ContextSnapshotEvent extends BaseEvent {
  type: EventType.ContextSnapshot;
  node: string;
  sections: ContextSection[];
}

export type RunEvent =
  | RunStartedEvent
  | RouterDecisionEvent
  | PlanCreatedEvent
  | AgentSpawnedEvent
  | NodeStartedEvent
  | ToolCallStartedEvent
  | ToolCallFinishedEvent
  | TokenUsageEvent
  | LlmCallStartedEvent
  | EmbeddingUsageEvent
  | NodeFinishedEvent
  | ApprovalRequiredEvent
  | ClarifyRequiredEvent
  | AnswerDeltaEvent
  | RunFinishedEvent
  | RunFailedEvent
  | ContextSnapshotEvent;

export function isTerminal(event: RunEvent): boolean {
  return event.type === EventType.RunFinished || event.type === EventType.RunFailed;
}
