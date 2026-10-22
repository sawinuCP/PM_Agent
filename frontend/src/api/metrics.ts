/** Cost/usage aggregation over token_usage events (plan §7.4 cost transparency). */

import { EventType, type RunEvent } from "./events";
import type { RunDetail } from "./types";

export interface UsageTotals {
  prompt: number;
  completion: number;
  tokens: number;
  costUsd: number;
  calls: number;
  embedTokens: number;
  embedCostUsd: number;
  embedCalls: number;
  embedMs: number;
}

function emptyUsage(): UsageTotals {
  return {
    prompt: 0, completion: 0, tokens: 0, costUsd: 0, calls: 0,
    embedTokens: 0, embedCostUsd: 0, embedCalls: 0, embedMs: 0,
  };
}

function fromRecord(record: Record<string, unknown> | null | undefined): UsageTotals {
  const totals = emptyUsage();
  if (!record) return totals;
  const num = (key: string): number => {
    const value = record[key];
    return typeof value === "number" ? value : Number(value) || 0;
  };
  totals.prompt = num("prompt");
  totals.completion = num("completion");
  totals.tokens = num("tokens") || totals.prompt + totals.completion;
  totals.costUsd = num("cost_usd");
  totals.calls = num("calls");
  totals.embedTokens = num("embed_tokens");
  totals.embedCostUsd = num("embed_cost_usd");
  totals.embedCalls = num("embed_calls");
  totals.embedMs = num("embed_ms");
  return totals;
}

export function aggregateUsage(
  events: RunEvent[],
  runDetail?: RunDetail | null,
): UsageTotals {
  const totals = emptyUsage();
  for (const e of events) {
    if (e.type === EventType.TokenUsage) {
      totals.prompt += e.prompt;
      totals.completion += e.completion;
      totals.tokens += e.prompt + e.completion;
      totals.costUsd += e.cost_usd;
      totals.calls += 1;
    }
    if (e.type === EventType.EmbeddingUsage) {
      totals.embedTokens += e.tokens;
      totals.embedCostUsd += e.cost_usd;
      totals.embedCalls += 1;
      totals.embedMs += e.ms;
      totals.costUsd += e.cost_usd;
    }
  }
  if (totals.calls === 0) {
    const finished = [...events].reverse().find((e) => e.type === EventType.RunFinished);
    if (finished && "totals" in finished) {
      const fromFinish = fromRecord(finished.totals as Record<string, unknown>);
      if (fromFinish.tokens || fromFinish.calls || fromFinish.costUsd) {
        Object.assign(totals, fromFinish);
      }
    }
  }
  if (totals.calls === 0 && totals.tokens === 0 && runDetail) {
    const fromDetail = fromRecord(runDetail.token_usage as Record<string, unknown> | null);
    if (runDetail.cost_usd && !fromDetail.costUsd) fromDetail.costUsd = runDetail.cost_usd;
    if (fromDetail.tokens || fromDetail.costUsd) Object.assign(totals, fromDetail);
  }
  totals.costUsd = Math.round(totals.costUsd * 1e6) / 1e6;
  totals.embedCostUsd = Math.round(totals.embedCostUsd * 1e6) / 1e6;
  return totals;
}
