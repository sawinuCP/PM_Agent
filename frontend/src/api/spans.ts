/** Derive OpenTelemetry-style spans from the existing SSE event stream. */

import { eventLatencyMs } from "./activityStats";
import { EventType, planSteps, type RunEvent } from "./events";

export type SpanKind = "run" | "router" | "plan" | "agent" | "tool" | "llm" | "embed" | "answer";

export interface TraceSpan {
  id: string;
  kind: SpanKind;
  name: string;
  startMs: number;
  endMs: number;
  status: "ok" | "error" | "running";
  tokens?: number;
  costUsd?: number;
  detail?: string;
  resultId?: string;
  node?: string;
  parentId?: string;
  concurrency?: "parallel" | "serial";
}

export interface SpanNode {
  span: TraceSpan;
  children: SpanNode[];
}

const KIND_ORDER: SpanKind[] = ["run", "router", "plan", "agent", "tool", "llm", "embed", "answer"];
const SKIP_FOR_GAPS = new Set<EventType>([EventType.AnswerDelta]);

function at(iso: string, fallback: number): number {
  const value = Date.parse(iso);
  return Number.isNaN(value) ? fallback : value;
}

function parseTs(iso: string): number | null {
  const value = Date.parse(iso);
  return Number.isNaN(value) ? null : value;
}

function earlierEvent(events: RunEvent[], index: number): RunEvent | null {
  const current = parseTs(events[index]?.ts ?? "");
  if (current == null) return null;
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const candidate = events[cursor];
    if (SKIP_FOR_GAPS.has(candidate.type)) continue;
    const stamp = parseTs(candidate.ts);
    if (stamp != null && stamp < current) return candidate;
  }
  return null;
}

function laterEvent(events: RunEvent[], index: number): RunEvent | null {
  const current = parseTs(events[index]?.ts ?? "");
  if (current == null) return null;
  for (let cursor = index + 1; cursor < events.length; cursor += 1) {
    const candidate = events[cursor];
    if (SKIP_FOR_GAPS.has(candidate.type)) continue;
    const stamp = parseTs(candidate.ts);
    if (stamp != null && stamp > current) return candidate;
  }
  return null;
}

const INHERIT_FOR_USAGE = new Set<EventType>([
  EventType.RouterDecision,
  EventType.PlanCreated,
  EventType.NodeStarted,
  EventType.NodeFinished,
]);

function windowFromLatency(
  events: RunEvent[],
  index: number,
  rel: (iso: string) => number,
): { startMs: number; endMs: number } | null {
  const event = events[index];
  const endMs = rel(event.ts);
  const explicit = "ms" in event && typeof event.ms === "number" && event.ms > 0
    ? event.ms
    : null;
  const latency = explicit ?? eventLatencyMs(events, index);
  if (latency != null && latency > 0) {
    return { startMs: Math.max(0, endMs - latency), endMs };
  }
  return null;
}

/** Wall-clock window for a point event, matching Activity step latency. */
export function eventWindow(
  events: RunEvent[],
  index: number,
  rel: (iso: string) => number,
): { startMs: number; endMs: number } {
  const event = events[index];
  const endMs = rel(event.ts);
  const direct = windowFromLatency(events, index, rel);
  if (direct) return direct;

  // token_usage is often emitted in the same instant as the decision it describes.
  if (event.type === EventType.TokenUsage) {
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      const previous = events[cursor];
      if (SKIP_FOR_GAPS.has(previous.type)) continue;
      if (!INHERIT_FOR_USAGE.has(previous.type)) break;
      const inherited = windowFromLatency(events, cursor, rel);
      if (inherited) {
        return { startMs: inherited.startMs, endMs: Math.max(endMs, inherited.endMs) };
      }
      break;
    }
  }

  const earlier = earlierEvent(events, index);
  if (earlier) {
    const startMs = rel(earlier.ts);
    if (endMs > startMs) return { startMs, endMs };
  }
  const later = laterEvent(events, index);
  if (later) {
    const laterMs = rel(later.ts);
    if (laterMs > endMs) return { startMs: endMs, endMs: laterMs };
  }
  return { startMs: endMs, endMs };
}

export function buildSpans(events: RunEvent[]): { spans: TraceSpan[]; durationMs: number } {
  if (events.length === 0) return { spans: [], durationMs: 0 };
  const origin = at(events[0].ts, Date.now());
  const last = at(events[events.length - 1].ts, origin);
  const rel = (iso: string) => Math.max(0, at(iso, origin) - origin);
  const spans: TraceSpan[] = [];

  const started = events.find((e) => e.type === EventType.RunStarted);
  const finished = [...events].reverse().find((e) =>
    e.type === EventType.RunFinished || e.type === EventType.RunFailed);
  spans.push({
    id: "span-run",
    kind: "run",
    name: "Trace",
    startMs: started ? rel(started.ts) : 0,
    endMs: finished ? rel(finished.ts) : rel(events[events.length - 1].ts),
    status: finished?.type === EventType.RunFailed ? "error"
      : finished ? "ok" : "running",
    detail: started && "goal" in started ? started.goal : undefined,
  });

  events.forEach((event, index) => {
    const window = eventWindow(events, index, rel);
    if (event.type === EventType.RouterDecision) {
      spans.push({
        id: `span-router-${event.seq}`,
        kind: "router",
        name: "Router",
        startMs: window.startMs,
        endMs: window.endMs,
        status: "ok",
        detail: `${event.intent} → ${event.path}`,
      });
    }
    if (event.type === EventType.PlanCreated) {
      spans.push({
        id: `span-plan-${event.seq}`,
        kind: "plan",
        name: "Planner",
        startMs: window.startMs,
        endMs: window.endMs,
        status: "ok",
        detail: (() => {
          const count = planSteps(event.plan.steps).length;
          return `${count} step${count === 1 ? "" : "s"}`;
        })(),
      });
    }
    if (event.type === EventType.TokenUsage) {
      spans.push({
        id: `span-llm-${event.seq}`,
        kind: "llm",
        name: event.node === "synthesis" ? "Synthesis" : `LLM · ${event.node}`,
        startMs: window.startMs,
        endMs: window.endMs,
        status: "ok",
        tokens: event.prompt + event.completion,
        costUsd: event.cost_usd,
        node: event.node,
        detail: `${event.prompt.toLocaleString()} in / ${event.completion.toLocaleString()} out`,
      });
    }
    if (event.type === EventType.EmbeddingUsage) {
      spans.push({
        id: `span-embed-${event.seq}`,
        kind: "embed",
        name: `Embed · ${event.model || event.provider}`,
        startMs: event.ms > 0 ? Math.max(0, rel(event.ts) - event.ms) : window.startMs,
        endMs: rel(event.ts),
        status: "ok",
        tokens: event.tokens,
        costUsd: event.cost_usd,
        node: event.node,
        detail: `${event.input_type || "embed"} · ${event.cached ? "cached" : "live"}`,
      });
    }
  });

  const nodeStarts = new Map<string, RunEvent>();
  for (const event of events) {
    if (event.type === EventType.NodeStarted) nodeStarts.set(event.node, event);
    if (event.type === EventType.NodeFinished) {
      const begin = nodeStarts.get(event.node) ?? event;
      spans.push({
        id: `span-node-${event.node}-${event.seq}`,
        kind: "agent",
        name: event.node === "synthesis" ? "Aggregator" : `Agent · ${event.node}`,
        startMs: rel(begin.ts),
        endMs: rel(event.ts),
        status: "ok",
        node: event.node,
        detail: event.summary,
      });
      nodeStarts.delete(event.node);
    }
  }
  for (const [node, begin] of nodeStarts) {
    spans.push({
      id: `span-node-live-${node}`,
      kind: "agent",
      name: `Agent · ${node}`,
      startMs: rel(begin.ts),
      endMs: rel(events[events.length - 1].ts),
      status: "running",
      node,
    });
  }

  const toolStarts = new Map<string, Extract<RunEvent, { type: EventType.ToolCallStarted }>>();
  for (const event of events) {
    if (event.type === EventType.ToolCallStarted) {
      toolStarts.set(`${event.step_id}:${event.tool}`, event);
    }
    if (event.type === EventType.ToolCallFinished) {
      const key = `${event.step_id}:${event.tool}`;
      const begin = toolStarts.get(key);
      const startedAt = rel((begin ?? event).ts);
      const endedAt = event.ms > 0 ? startedAt + event.ms : rel(event.ts);
      spans.push({
        id: `span-tool-${event.seq}`,
        kind: "tool",
        name: event.tool.replaceAll("_", " "),
        startMs: startedAt,
        endMs: Math.max(startedAt, endedAt),
        status: event.status === "ok" ? "ok" : "error",
        detail: event.digest_preview,
        resultId: event.result_id ?? undefined,
        node: event.step_id,
        concurrency: event.mode === "parallel" || event.mode === "serial"
          ? event.mode
          : (begin && "mode" in begin && (begin.mode === "parallel" || begin.mode === "serial")
            ? begin.mode
            : undefined),
      });
      toolStarts.delete(key);
    }
  }

  const deltas = events.filter((e) => e.type === EventType.AnswerDelta);
  if (deltas.length > 0) {
    const lastDelta = deltas[deltas.length - 1];
    spans.push({
      id: "span-answer",
      kind: "answer",
      name: "Answer stream",
      startMs: rel(deltas[0].ts),
      endMs: rel(lastDelta.ts),
      status: lastDelta.type === EventType.AnswerDelta && lastDelta.done ? "ok" : "running",
      detail: `${deltas.length} chunk${deltas.length === 1 ? "" : "s"}`,
    });
  }

  spans.sort((a, b) =>
    a.startMs - b.startMs || KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
  alignPhaseSpansToLlm(spans);
  assignParents(spans);
  expandParentsToChildren(spans);
  // Memory writes land after run_finished. Do not stretch the user-visible
  // trace to wait on Voyage / post-run extraction.
  if (finished) {
    const cap = rel(finished.ts);
    const run = spans.find((span) => span.kind === "run");
    if (run) run.endMs = cap;
    return { spans, durationMs: Math.max(cap, 1) };
  }
  const durationMs = Math.max(last - origin, ...spans.map((s) => s.endMs), 1);
  return { spans, durationMs };
}

function expandParentsToChildren(spans: TraceSpan[]): void {
  const byId = new Map(spans.map((span) => [span.id, span]));
  for (let pass = 0; pass < 3; pass += 1) {
    for (const span of spans) {
      if (!span.parentId) continue;
      const parent = byId.get(span.parentId);
      if (!parent) continue;
      parent.startMs = Math.min(parent.startMs, span.startMs);
      parent.endMs = Math.max(parent.endMs, span.endMs);
    }
  }
}

function alignPhaseSpansToLlm(spans: TraceSpan[]): void {
  for (const phase of ["router", "planner"] as const) {
    const phaseSpan = spans.find((span) => span.kind === (phase === "router" ? "router" : "plan"));
    const llm = spans.find((span) => span.kind === "llm" && span.node === phase);
    if (phaseSpan && llm) {
      phaseSpan.startMs = llm.startMs;
      phaseSpan.endMs = llm.endMs;
    }
  }
}

function assignParents(spans: TraceSpan[]): void {
  const run = spans.find((span) => span.kind === "run");
  const router = spans.find((span) => span.kind === "router");
  const plan = spans.find((span) => span.kind === "plan");
  const agents = new Map<string, TraceSpan>();
  for (const span of spans) {
    if (span.kind === "agent" && span.node) agents.set(span.node, span);
  }
  for (const span of spans) {
    if (span.kind === "run") continue;
    if (span.kind === "router" || span.kind === "plan" || span.kind === "answer" || span.kind === "agent") {
      span.parentId = run?.id;
      continue;
    }
    if (span.kind === "llm") {
      if (span.node === "router" && router) span.parentId = router.id;
      else if (span.node === "planner" && plan) span.parentId = plan.id;
      else if (span.node && agents.has(span.node)) span.parentId = agents.get(span.node)?.id;
      else if (span.node?.includes(":")) {
        const base = span.node.split(":")[0] ?? "";
        if (agents.has(base)) span.parentId = agents.get(base)?.id;
      } else span.parentId = run?.id;
      continue;
    }
    if (span.kind === "tool") {
      span.parentId = (span.node && agents.get(span.node)?.id) || run?.id;
      continue;
    }
    span.parentId = run?.id;
  }
}

export function nestSpans(spans: TraceSpan[]): SpanNode[] {
  const nodes = new Map<string, SpanNode>(
    spans.map((span) => [span.id, { span, children: [] }]),
  );
  const roots: SpanNode[] = [];
  for (const span of spans) {
    const node = nodes.get(span.id);
    if (!node) continue;
    const parent = span.parentId ? nodes.get(span.parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}
