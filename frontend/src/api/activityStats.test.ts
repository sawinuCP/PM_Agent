import { describe, expect, it } from "vitest";

import { EventType, type RunEvent } from "./events";
import { categoryStats, eventLatencyMs, eventTokens, stepStats } from "./activityStats";

const ev = (over: Partial<RunEvent> & { type: EventType; seq: number; ts: string }): RunEvent =>
  ({ run_id: "r", ...over } as RunEvent);

describe("activity step stats", () => {
  const events: RunEvent[] = [
    ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00.000Z", goal: "hi" }),
    ev({
      type: EventType.EmbeddingUsage, seq: 1, ts: "2026-09-05T12:00:00.040Z",
      node: "memory.retrieve", model: "voyage-3.5-lite", provider: "voyage",
      tokens: 12, cost_usd: 0.000001, ms: 38, input_type: "query", cached: false,
    }),
    ev({
      type: EventType.TokenUsage, seq: 2, ts: "2026-09-05T12:00:00.200Z",
      node: "fast", prompt: 199, completion: 108, cost_usd: 0.00006,
    }),
    ev({ type: EventType.RunFinished, seq: 3, ts: "2026-09-05T12:00:00.280Z", result: "ok", totals: {} }),
  ];

  it("uses explicit ms on embedding and tool events", () => {
    expect(eventLatencyMs(events, 1)).toBe(38);
    expect(eventTokens(events[1])).toBe(12);
  });

  it("derives latency from timestamps when ms is missing", () => {
    expect(eventLatencyMs(events, 2)).toBe(160);
    expect(stepStats(events, 2).tokens).toBe(307);
  });

  it("rolls category chips up with latency, tokens, and cost", () => {
    const usage = categoryStats(events, ["token_usage", "embedding_usage"]);
    expect(usage.count).toBe(2);
    expect(usage.tokens).toBe(319);
    expect(usage.costUsd).toBeCloseTo(0.000061, 6);
    expect(usage.ms).toBe(198);
  });
});
