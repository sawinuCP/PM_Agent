/**
 * useRunEvents — run trace subscription.
 *
 * Two modes (separate transports, one state shape via reduceEvent):
 *  - "live":   fetch-SSE stream of /runs/{id}/events (incremental frames)
 *  - "replay": fetch /runs/{id}/traces once and hydrate the same state shape
 */

import { useCallback, useEffect, useState } from "react";

import {
  API_BASE, api, apiHeaders, parseSse, reduceEvent, replayState, traceStateEqual,
  type TraceState,
} from "../api/client";
import { isTerminal } from "../api/events";

const EMPTY: TraceState = {
  events: [], status: "idle", pendingApproval: null, pendingClarify: null, partialAnswer: "",
};

export function useRunEvents(
  runId: string | null,
  mode: "live" | "replay" = "live",
  /** Bump to force a fresh live SSE subscription (e.g. clarify resume on same run). */
  generation = 0,
): TraceState & { approve: (approved: boolean) => Promise<void> } {
  const [state, setState] = useState<TraceState>(EMPTY);

  const applyState = useCallback((next: TraceState) => {
    setState((current) => (traceStateEqual(current, next) ? current : next));
  }, []);

  useEffect(() => {
    if (!runId) {
      setState(EMPTY);
      return;
    }
    if (mode === "replay") {
      let cancelled = false;
      (async () => {
        while (!cancelled) {
          try {
            const { events } = await api.runTraces(runId);
            if (cancelled) return;
            const next = replayState(events);
            applyState(next);
            if (next.status === "completed" || next.status === "failed") return;
            if (next.pendingClarify || next.pendingApproval) return;
            if (!next.pendingClarify && !next.pendingApproval
                && next.status === "streaming" && events.length > 0) {
              const run = await api.runDetail(runId).catch(() => null);
              if (cancelled) return;
              if (run?.status === "awaiting_clarification" && run.result) {
                applyState({
                  ...next,
                  pendingClarify: run.result,
                  partialAnswer: run.result || next.partialAnswer,
                });
              } else if (run?.status === "running") {
                applyState({ ...next, pendingClarify: null });
              }
            }
          } catch {
            if (!cancelled) setState((current) => ({ ...current, status: "failed" }));
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 750));
        }
      })();
      return () => { cancelled = true; };
    }

    const controller = new AbortController();
    setState({ events: [], status: "streaming", pendingApproval: null, pendingClarify: null, partialAnswer: "" });
    (async () => {
      let terminal = false;
      try {
        const res = await fetch(`${API_BASE}/runs/${runId}/events`, {
          signal: controller.signal,
          headers: apiHeaders({ Accept: "text/event-stream" }),
        });
        if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const sseState = { buffer: "" };
        for (;;) {
          const { done, value } = await reader.read();
          if (done) {
            const parsed = parseSse(decoder.decode(), sseState);
            if (sseState.buffer.trim()) {
              parsed.push(...parseSse("\n\n", sseState));
            }
            setState((prev) => parsed.reduce(reduceEvent, prev));
            terminal = parsed.some(isTerminal);
            break;
          }
          const parsed = parseSse(decoder.decode(value, { stream: true }), sseState);
          setState((prev) => parsed.reduce(reduceEvent, prev));
          if (parsed.some(isTerminal)) {
            terminal = true;
            break;
          }
        }
        if (!terminal && !controller.signal.aborted) {
          // Development proxies and brief network interruptions can close an
          // SSE response while the backend run continues. Recover from the
          // persisted trace until a terminal event arrives.
          while (!controller.signal.aborted) {
            try {
              const { events } = await api.runTraces(runId);
              const recovered = replayState(events);
              applyState(recovered);
              if (recovered.status === "completed" || recovered.status === "failed") break;
              if (!recovered.pendingClarify && !recovered.pendingApproval) {
                const run = await api.runDetail(runId).catch(() => null);
                if (controller.signal.aborted) break;
                if (run?.status === "awaiting_clarification" && run.result) {
                  applyState({
                    ...recovered,
                    pendingClarify: run.result,
                    partialAnswer: run.result || recovered.partialAnswer,
                  });
                  break;
                }
                if (run?.status === "running") {
                  applyState({ ...recovered, pendingClarify: null });
                }
              } else {
                break;
              }
            } catch {
              // Keep the current progress visible and retry.
            }
            await new Promise((resolve) => window.setTimeout(resolve, 750));
          }
        }
      } catch {
        if (!controller.signal.aborted) {
          try {
            const { events } = await api.runTraces(runId);
            applyState(replayState(events));
          } catch {
            setState((prev) => ({ ...prev, status: "failed" }));
          }
        }
      }
    })();
    return () => controller.abort();
  }, [runId, mode, generation, applyState]);

  const approve = useCallback(async (approved: boolean) => {
    if (!runId) throw new Error("No active run");
    let pending: string | null = null;
    setState((current) => {
      pending = current.pendingApproval;
      return { ...current, pendingApproval: null };
    });
    try {
      await api.approve(runId, approved);
    } catch (error) {
      setState((current) => ({ ...current, pendingApproval: pending }));
      throw error;
    }
  }, [runId]);
  return { ...state, approve };
}

export type TraceConnection = "idle" | "sse" | "polling" | "replay";

export type { RunEvent } from "../api/events";
