import type { RunEvent } from "../../api/events";
import {
  Bot, CircleAlert, CircleCheck, CirclePlay, Coins, Compass, ListChecks,
  Play, ScanSearch, Wrench, XCircle, type LucideIcon,
} from "lucide-react";

/** Per-event-type icon mapping — one source of truth for trace + activity. */
export const EVENT_META: Record<string, { tone: string; Icon: LucideIcon }> = {
  run_started: { tone: "accent", Icon: Play },
  router_decision: { tone: "info", Icon: Compass },
  plan_created: { tone: "accent", Icon: ListChecks },
  agent_spawned: { tone: "accent", Icon: Bot },
  node_started: { tone: "info", Icon: CirclePlay },
  node_finished: { tone: "ok", Icon: CircleCheck },
  tool_call_started: { tone: "info", Icon: Wrench },
  tool_call_finished: { tone: "ok", Icon: Wrench },
  token_usage: { tone: "warn", Icon: Coins },
  llm_call_started: { tone: "info", Icon: CirclePlay },
  embedding_usage: { tone: "info", Icon: ScanSearch },
  approval_required: { tone: "warn", Icon: CircleAlert },
  clarify_required: { tone: "warn", Icon: CircleAlert },
  answer_delta: { tone: "accent", Icon: Play },
  run_finished: { tone: "ok", Icon: CircleCheck },
  run_failed: { tone: "err", Icon: XCircle },
};

/** Run-level status → dot tone. */
export function statusTone(status: string): string {
  if (status === "completed" || status === "ok") return "ok";
  if (status === "failed" || status === "error") return "err";
  if (status === "running" || status === "awaiting_approval" || status === "awaiting_clarification") return "warn";
  return "idle";
}
