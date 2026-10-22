/** Shared DTOs mirroring backend response schemas (§8.1). */

export interface SessionOut {
  id: string;
  title: string;
  thread_id: string;
  status: string;
  message_count: number;
  total_tokens: number;
  total_cost_usd: number;
  created_at: string;
  updated_at: string;
}

export interface SessionContextStats {
  transcript_tokens: number;
  memory_window_tokens: number;
  effective_tokens: number;
  budget_tokens: number;
  threshold_tokens: number;
  threshold_pct: number;
  utilization_pct: number;
  stored_message_tokens: number;
  message_count: number;
  has_summary: boolean;
  summarization_pending: boolean;
  session_context_chars: number;
  session_context_char_limit: number;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: { text?: string };
  run_id: string | null;
  token_count: number;
  created_at: string;
}

export interface RunSummary {
  id: string;
  status: string;
  goal: string;
  cost_usd: number;
  started_at: string;
  finished_at: string | null;
}

export interface RunDetail extends RunSummary {
  session_id: string;
  thread_id: string;
  plan: {
    steps?: {
      id: string;
      agent: string;
      task: string;
      tools?: string[];
      depends_on?: string[];
    }[];
    _interrupt?: Record<string, unknown>;
  } | null;
  result: string | null;
  error: string | null;
  token_usage: { tokens?: number; cost_usd?: number } | null;
}

export interface TelemetryBucket {
  bucket: string;
  traces: number;
  tokens: number;
  cost_usd: number;
  errors: number;
  avg_latency_ms: number | null;
}

export interface TelemetryRecent {
  id: string;
  status: string;
  goal: string;
  tokens: number;
  cost_usd: number;
  started_at: string;
  duration_ms: number | null;
}

export interface TelemetryTotals {
  traces: number;
  completed: number;
  failed: number;
  running: number;
  tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  llm_calls: number;
  avg_latency_ms: number | null;
  success_rate: number | null;
}

export interface TelemetryOut {
  range: "day" | "week" | "month";
  from_ts: string;
  to_ts: string;
  totals: TelemetryTotals;
  series: TelemetryBucket[];
  by_status: Record<string, number>;
  recent: TelemetryRecent[];
}

export interface ToolResultDetail {
  id: string;
  run_id: string;
  step_id: string;
  tool: string;
  args_digest: Record<string, unknown>;
  payload: unknown;
  digest: string;
  byte_size: number;
  status: string;
  created_at: string;
}
