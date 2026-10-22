import { describe, expect, it } from "vitest";

import { EventType, type RunEvent } from "./events";
import { buildSpans, nestSpans, type TraceSpan } from "./spans";

const ev = (over: Partial<RunEvent> & { type: EventType; seq: number; ts: string }): RunEvent =>
  ({ run_id: "r", ...over } as RunEvent);

describe("buildSpans", () => {
  it("builds a run span plus router, tool, and llm spans", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "tickets" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.ToolCallStarted, seq: 2, ts: "2026-09-05T12:00:02Z",
        step_id: "s1", tool: "jira_search", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 3, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_search", status: "ok", ms: 40,
        digest_preview: "3 issues", result_id: "tr-1" }),
      ev({ type: EventType.TokenUsage, seq: 4, ts: "2026-09-05T12:00:03Z",
        node: "s1", prompt: 10, completion: 4, cost_usd: 0.001 }),
      ev({ type: EventType.RunFinished, seq: 5, ts: "2026-09-05T12:00:04Z",
        result: "done", totals: {} }),
    ];
    const { spans, durationMs } = buildSpans(events);
    expect(durationMs).toBeGreaterThan(0);
    expect(spans.some((s) => s.kind === "run" && s.status === "ok")).toBe(true);
    expect(spans.some((s) => s.kind === "router" && s.detail?.includes("plan"))).toBe(true);
    expect(spans.some((s) => s.kind === "tool" && s.resultId === "tr-1")).toBe(true);
    expect(spans.some((s) => s.kind === "llm" && s.tokens === 14)).toBe(true);
    const router = spans.find((s) => s.kind === "router");
    expect(router && router.endMs - router.startMs).toBe(1000);
    const tool = spans.find((s) => s.kind === "tool");
    expect(tool && tool.endMs - tool.startMs).toBe(40);
  });

  it("nests tools and model calls under the agent, and everything under Trace", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "tickets" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.TokenUsage, seq: 2, ts: "2026-09-05T12:00:01Z",
        node: "router", prompt: 20, completion: 4, cost_usd: 0.0001 }),
      ev({ type: EventType.NodeStarted, seq: 3, ts: "2026-09-05T12:00:02Z", node: "s1" }),
      ev({ type: EventType.ToolCallStarted, seq: 4, ts: "2026-09-05T12:00:02Z",
        step_id: "s1", tool: "jira_search", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 5, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_search", status: "ok", ms: 40,
        digest_preview: "3 issues", result_id: "tr-1" }),
      ev({ type: EventType.TokenUsage, seq: 6, ts: "2026-09-05T12:00:03Z",
        node: "s1", prompt: 10, completion: 4, cost_usd: 0.001 }),
      ev({ type: EventType.NodeFinished, seq: 7, ts: "2026-09-05T12:00:04Z",
        node: "s1", summary: "listed issues" }),
      ev({ type: EventType.RunFinished, seq: 8, ts: "2026-09-05T12:00:04Z",
        result: "done", totals: {} }),
    ];
    const { spans } = buildSpans(events);
    const tree = nestSpans(spans);
    expect(tree).toHaveLength(1);
    expect(tree[0].span.kind).toBe("run");
    const kinds = tree[0].children.map((child) => child.span.kind);
    expect(kinds).toContain("router");
    expect(kinds).toContain("agent");
    const router = tree[0].children.find((child) => child.span.kind === "router");
    expect(router?.children.some((child) => child.span.kind === "llm" && child.span.node === "router")).toBe(true);
    const agent = tree[0].children.find((child) => child.span.kind === "agent");
    expect(agent?.children.some((child) => child.span.kind === "tool")).toBe(true);
    expect(agent?.children.some((child) => child.span.kind === "llm")).toBe(true);
    const routerLlm = router?.children.find((child) => child.span.kind === "llm");
    expect(routerLlm && routerLlm.span.endMs - routerLlm.span.startMs).toBe(1000);
    const tool = agent?.children.find((child) => child.span.kind === "tool");
    expect(tool && tool.span.endMs - tool.span.startMs).toBe(40);
  });

  it("gives every step the same latency Activity shows, including same-instant LLM usage", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00.000Z", goal: "tickets" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:04.000Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.TokenUsage, seq: 2, ts: "2026-09-05T12:00:04.000Z",
        node: "router", prompt: 20, completion: 4, cost_usd: 0.0001 }),
      ev({ type: EventType.TokenUsage, seq: 3, ts: "2026-09-05T12:00:12.000Z",
        node: "planner", prompt: 80, completion: 30, cost_usd: 0.002 }),
      ev({ type: EventType.PlanCreated, seq: 4, ts: "2026-09-05T12:00:12.000Z",
        plan: { steps: [{ id: "s1", agent: "jira", task: "list", tools: [], depends_on: [] }],
          synthesis: "", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.NodeStarted, seq: 5, ts: "2026-09-05T12:00:12.200Z", node: "s1" }),
      ev({ type: EventType.TokenUsage, seq: 6, ts: "2026-09-05T12:00:40.000Z",
        node: "s1", prompt: 10, completion: 4, cost_usd: 0.001 }),
      ev({ type: EventType.ToolCallStarted, seq: 7, ts: "2026-09-05T12:00:40.000Z",
        step_id: "s1", tool: "jira_search", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 8, ts: "2026-09-05T12:00:40.161Z",
        step_id: "s1", tool: "jira_search", status: "ok", ms: 161,
        digest_preview: "3 issues", result_id: "tr-1" }),
      ev({ type: EventType.NodeFinished, seq: 9, ts: "2026-09-05T12:00:41.000Z",
        node: "s1", summary: "listed issues" }),
      ev({ type: EventType.TokenUsage, seq: 10, ts: "2026-09-05T12:00:50.000Z",
        node: "synthesis", prompt: 40, completion: 20, cost_usd: 0.001 }),
      ev({ type: EventType.RunFinished, seq: 11, ts: "2026-09-05T12:00:50.200Z",
        result: "done", totals: {} }),
    ];
    const { spans } = buildSpans(events);
    const byKind = (kind: TraceSpan["kind"], node?: string) =>
      spans.find((span) => span.kind === kind && (node == null || span.node === node));
    expect(byKind("router") && byKind("router")!.endMs - byKind("router")!.startMs).toBe(4000);
    expect(byKind("llm", "router") && byKind("llm", "router")!.endMs - byKind("llm", "router")!.startMs).toBe(4000);
    expect(byKind("llm", "planner") && byKind("llm", "planner")!.endMs - byKind("llm", "planner")!.startMs).toBe(8000);
    expect(byKind("plan") && byKind("plan")!.endMs - byKind("plan")!.startMs).toBe(8000);
    expect(byKind("llm", "s1") && byKind("llm", "s1")!.endMs - byKind("llm", "s1")!.startMs).toBe(27800);
    expect(byKind("tool") && byKind("tool")!.endMs - byKind("tool")!.startMs).toBe(161);
    expect(byKind("llm", "synthesis") && byKind("llm", "synthesis")!.endMs - byKind("llm", "synthesis")!.startMs).toBe(9000);
  });

  it("does not stretch the trace to post-finish memory embeddings", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00.000Z", goal: "tickets" }),
      ev({ type: EventType.TokenUsage, seq: 1, ts: "2026-09-05T12:00:08.000Z",
        node: "synthesis", prompt: 40, completion: 20, cost_usd: 0.001 }),
      ev({ type: EventType.RunFinished, seq: 2, ts: "2026-09-05T12:00:08.200Z",
        result: "done", totals: {} }),
      ev({ type: EventType.EmbeddingUsage, seq: 3, ts: "2026-09-05T12:00:23.000Z",
        node: "memory.write", model: "voyage-3.5-lite", provider: "voyage",
        tokens: 12, cost_usd: 0, ms: 875, input_type: "document", cached: false }),
    ];
    const { spans, durationMs } = buildSpans(events);
    expect(durationMs).toBe(8200);
    const run = spans.find((span) => span.kind === "run");
    expect(run?.endMs).toBe(8200);
  });

  it("returns empty spans for an empty stream", () => {
    expect(buildSpans([])).toEqual({ spans: [], durationMs: 0 });
  });
});
