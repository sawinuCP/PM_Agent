/** Typed API client + SSE hooks (§8.1/§8.2). */

import { EventType, type RunEvent } from "./events";
import type {
  MessageOut, RunDetail, RunSummary, SessionContextStats, SessionOut, TelemetryOut, ToolResultDetail,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_URL ?? "/api";
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...(API_TOKEN ? { "X-API-Key": API_TOKEN } : {}),
    ...extra,
  };
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? body.error ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  createSession: (title = "New session"): Promise<SessionOut> =>
    fetch(`${API_BASE}/sessions`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ title }),
    }).then((r) => json<SessionOut>(r)),

  listSessions: (): Promise<SessionOut[]> =>
    fetch(`${API_BASE}/sessions`, { headers: apiHeaders() }).then((r) => json<SessionOut[]>(r)),

  getSession: (sessionId: string): Promise<SessionOut> =>
    fetch(`${API_BASE}/sessions/${sessionId}`, { headers: apiHeaders() }).then((r) => json(r)),

  deleteSession: (sessionId: string): Promise<void> =>
    fetch(`${API_BASE}/sessions/${sessionId}`, {
      method: "DELETE", headers: apiHeaders(),
    }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    }),

  listMessages: (sessionId: string): Promise<{ messages: MessageOut[]; count: number }> =>
    fetch(`${API_BASE}/sessions/${sessionId}/messages`, { headers: apiHeaders() }).then((r) => json(r)),

  sessionContextStats: (sessionId: string): Promise<SessionContextStats> =>
    fetch(`${API_BASE}/sessions/${sessionId}/context-stats`, { headers: apiHeaders() })
      .then((r) => json<SessionContextStats>(r)),

  listRuns: (sessionId: string): Promise<{ runs: RunSummary[] }> =>
    fetch(`${API_BASE}/sessions/${sessionId}/runs`, { headers: apiHeaders() }).then((r) => json(r)),

  sendMessage: (sessionId: string, message: string): Promise<{ run_id: string }> =>
    fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: sessionId, message }),
    }).then((r) => json<{ run_id: string }>(r)),

  runDetail: (runId: string): Promise<RunDetail> =>
    fetch(`${API_BASE}/runs/${runId}`, { headers: apiHeaders() }).then((r) => json<RunDetail>(r)),

  runTraces: (runId: string): Promise<{ events: RunEvent[]; count: number }> =>
    fetch(`${API_BASE}/runs/${runId}/traces`, { headers: apiHeaders() }).then((r) => json(r)),

  toolResult: (runId: string, resultId: string): Promise<ToolResultDetail> =>
    fetch(`${API_BASE}/runs/${runId}/tool-results/${resultId}`, { headers: apiHeaders() }).then((r) => json<ToolResultDetail>(r)),

  telemetry: (range: "day" | "week" | "month" = "week"): Promise<TelemetryOut> =>
    fetch(`${API_BASE}/telemetry?range=${range}`, { headers: apiHeaders() })
      .then((r) => json<TelemetryOut>(r)),

  approve: (runId: string, approved: boolean): Promise<unknown> =>
    fetch(`${API_BASE}/runs/${runId}/approve`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ approved }),
    }).then((r) => json(r)),
};

/**
 * parseSse — incremental text/event-stream parser (frames separated by a
 * blank line; `data:` payloads only; ignores comments/heartbeats).
 *
 * CRLF-safe: sse-starlette terminates lines with \r\n, so we normalize to \n
 * on append. Chunk boundaries may split a \r\n pair — normalizing the whole
 * buffer each time still reassembles it correctly.
 */
export function parseSse(chunk: string, state: { buffer: string }): RunEvent[] {
  state.buffer += chunk.replace(/\r\n/g, "\n");
  const events: RunEvent[] = [];
  let index: number;
  while ((index = state.buffer.indexOf("\n\n")) !== -1) {
    const frame = state.buffer.slice(0, index);
    state.buffer = state.buffer.slice(index + 2);
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) {
        const data = line.slice(5).trim();
        if (data) {
          try {
            events.push(JSON.parse(data) as RunEvent);
          } catch {
            /* malformed frame — skipped, never fatal */
          }
        }
      }
    }
  }
  return events;
}

export interface TraceState {
  events: RunEvent[];
  status: "idle" | "streaming" | "completed" | "failed";
  pendingApproval: string | null;
  pendingClarify: string | null;
  partialAnswer: string;
}

/** Avoid re-render storms when replay polling returns an unchanged trace snapshot. */
export function traceStateEqual(a: TraceState, b: TraceState): boolean {
  if (a.status !== b.status
    || a.pendingApproval !== b.pendingApproval
    || a.pendingClarify !== b.pendingClarify
    || a.partialAnswer !== b.partialAnswer
    || a.events.length !== b.events.length) {
    return false;
  }
  if (a.events.length === 0) return true;
  return a.events.at(-1)?.seq === b.events.at(-1)?.seq;
}

function deltasOf(events: RunEvent[]): string {
  return events
    .filter((event): event is Extract<RunEvent, { type: EventType.AnswerDelta }> =>
      event.type === EventType.AnswerDelta)
    .map((event) => event.text)
    .join("");
}

/** True when execution resumed after the most recent clarify_required event. */
export function clarifyResolved(events: RunEvent[]): boolean {
  let lastClarify = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === EventType.ClarifyRequired) {
      lastClarify = index;
      break;
    }
  }
  if (lastClarify < 0) return false;
  return events.slice(lastClarify + 1).some((event) =>
    event.type === EventType.PlanCreated
    || event.type === EventType.AgentSpawned
    || event.type === EventType.RunFinished
    || event.type === EventType.RunFailed
    || event.type === EventType.ClarifyRequired);
}

function clearClarifyUi(state: TraceState): Partial<TraceState> {
  const hadClarify = state.pendingClarify !== null;
  const hasDeltas = state.events.some((event) => event.type === EventType.AnswerDelta);
  return {
    pendingClarify: null,
    partialAnswer: hadClarify && !hasDeltas ? "" : state.partialAnswer,
  };
}

export function reduceEvent(state: TraceState, event: RunEvent): TraceState {
  const events = [...state.events, event];
  if (event.type === EventType.AnswerDelta) {
    const fresh = state.pendingClarify !== null;
    return {
      ...state,
      events,
      status: "streaming",
      pendingClarify: null,
      partialAnswer: fresh ? event.text : state.partialAnswer + event.text,
    };
  }
  if (event.type === EventType.ApprovalRequired) {
    return { ...state, events, status: "streaming", pendingApproval: event.pending_tool };
  }
  if (event.type === EventType.ClarifyRequired) {
    return {
      ...state,
      events,
      status: "streaming",
      pendingClarify: event.question,
      partialAnswer: state.partialAnswer,
    };
  }
  if (event.type === EventType.PlanCreated || event.type === EventType.AgentSpawned) {
    return {
      ...state,
      events,
      status: "streaming",
      ...clearClarifyUi(state),
    };
  }
  if (event.type === EventType.RunFinished) {
    return {
      ...state,
      events,
      status: "completed",
      pendingApproval: null,
      pendingClarify: null,
      partialAnswer: event.result || state.partialAnswer,
    };
  }
  if (event.type === EventType.RunFailed) {
    return { ...state, events, status: "failed", pendingApproval: null, pendingClarify: null };
  }
  return { ...state, events };
}

/** Last terminal event wins. Memory writes after run_finished must not reopen the run. */
export function terminalStatus(events: RunEvent[]): "completed" | "failed" | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === EventType.RunFailed) return "failed";
    if (event.type === EventType.RunFinished) return "completed";
  }
  return null;
}

export function replayState(events: RunEvent[]): TraceState {
  const last = events.at(-1);
  const partialAnswer = deltasOf(events);
  const finished = [...events].reverse().find((event) => event.type === EventType.RunFinished);
  const terminal = terminalStatus(events);
  if (last?.type === EventType.ApprovalRequired && !terminal) {
    return {
      events,
      status: "streaming",
      pendingApproval: last.pending_tool,
      pendingClarify: null,
      partialAnswer,
    };
  }
  const clarify = [...events].reverse().find((event) => event.type === EventType.ClarifyRequired);
  if (clarify && !terminal && !clarifyResolved(events)) {
    return {
      events,
      status: "streaming",
      pendingApproval: null,
      pendingClarify: clarify.question,
      partialAnswer,
    };
  }
  return {
    events,
    status: terminal ?? (events.length > 0 ? "streaming" : "idle"),
    pendingApproval: null,
    pendingClarify: null,
    partialAnswer: (finished && "result" in finished && finished.result) || partialAnswer,
  };
}

