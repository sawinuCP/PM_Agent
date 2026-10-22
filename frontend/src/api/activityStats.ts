/** Per-step and per-category latency / tokens / cost for the Activity tab. */

import { EventType, type RunEvent } from "./events";

export interface StepStats {
  ms: number | null;
  tokens: number | null;
  costUsd: number | null;
}

export interface CategoryStats {
  count: number;
  ms: number;
  tokens: number;
  costUsd: number;
}

function parseTs(iso: string): number | null {
  const value = Date.parse(iso);
  return Number.isNaN(value) ? null : value;
}

export function eventTokens(event: RunEvent): number | null {
  if (event.type === EventType.TokenUsage) return event.prompt + event.completion;
  if (event.type === EventType.EmbeddingUsage) return event.tokens;
  return null;
}

export function eventCost(event: RunEvent): number | null {
  if (event.type === EventType.TokenUsage || event.type === EventType.EmbeddingUsage) {
    return event.cost_usd;
  }
  return null;
}

export function eventLatencyMs(events: RunEvent[], index: number): number | null {
  const event = events[index];
  if (!event) return null;
  if ("ms" in event && typeof event.ms === "number") return event.ms;
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const previous = events[cursor];
    if (previous.type === EventType.AnswerDelta) continue;
    const start = parseTs(previous.ts);
    const end = parseTs(event.ts);
    if (start == null || end == null) return null;
    return Math.max(0, end - start);
  }
  return 0;
}

export function stepStats(events: RunEvent[], index: number): StepStats {
  const event = events[index];
  return {
    ms: eventLatencyMs(events, index),
    tokens: event ? eventTokens(event) : null,
    costUsd: event ? eventCost(event) : null,
  };
}

export function categoryStats(
  events: RunEvent[],
  types: readonly string[] | "all",
): CategoryStats {
  const stats: CategoryStats = { count: 0, ms: 0, tokens: 0, costUsd: 0 };
  events.forEach((event, index) => {
    if (event.type === EventType.AnswerDelta) return;
    if (types !== "all" && !types.includes(event.type)) return;
    stats.count += 1;
    const step = stepStats(events, index);
    stats.ms += step.ms ?? 0;
    stats.tokens += step.tokens ?? 0;
    stats.costUsd += step.costUsd ?? 0;
  });
  stats.costUsd = Math.round(stats.costUsd * 1e6) / 1e6;
  return stats;
}

export function formatStepMs(ms: number | null | undefined): string | null {
  if (ms == null) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)}s`;
}
