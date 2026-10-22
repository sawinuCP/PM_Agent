/** SSE parser + event→state reducer tests (review §3.5 item 7: REQUIRED). */

import { describe, expect, it } from "vitest";

import { EventType, isTerminal, type RunEvent } from "./events";
import { parseSse, reduceEvent, replayState, traceStateEqual, clarifyResolved, type TraceState } from "./client";
import { aggregateUsage } from "./metrics";

const frame = (event: object): string => `data: ${JSON.stringify(event)}\n\n`;

const ev = (over: Partial<RunEvent> & { type: EventType }): RunEvent =>
  ({ run_id: "r", ts: "t", seq: 0, ...over } as RunEvent);

describe("parseSse", () => {
  it("parses a single data frame", () => {
    const state = { buffer: "" };
    const events = parseSse(
      frame({ type: "run_started", run_id: "r", ts: "t", seq: 0, goal: "g" }), state);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe(EventType.RunStarted);
  });

  it("buffers partial frames across chunks", () => {
    const state = { buffer: "" };
    const half = `data: {"type":"run_started","run_id":"r","ts":"t","seq":0`;
    expect(parseSse(half, state)).toHaveLength(0);
    const rest = `,"goal":"g"}\n\n`;
    const events = parseSse(rest, state);
    expect(events).toHaveLength(1);
  });

  it("parses multiple frames in one chunk", () => {
    const state = { buffer: "" };
    const chunk = frame({ type: "run_started", seq: 0 }) +
      frame({ type: "node_started", seq: 1, node: "s1" });
    expect(parseSse(chunk, state)).toHaveLength(2);
  });

  it("ignores keep-alive comments", () => {
    const state = { buffer: "" };
    expect(parseSse(": keep-alive\n\n", state)).toHaveLength(0);
  });
});

describe("reduceEvent", () => {
  const initial: TraceState = {
    events: [], status: "streaming", pendingApproval: null, pendingClarify: null, partialAnswer: "",
  };

  it("appends events immutably", () => {
    const next = reduceEvent(initial,
      ev({ type: EventType.NodeStarted, node: "s1" }) as RunEvent);
    expect(next.events).toHaveLength(1);
    expect(initial.events).toHaveLength(0);
  });

  it("captures pending clarify", () => {
    const next = reduceEvent(initial,
      ev({ type: EventType.ClarifyRequired, question: "Which project?" }) as RunEvent);
    expect(next.pendingClarify).toBe("Which project?");
    expect(next.partialAnswer).toBe("");
  });

  it("clears pending clarify after plan_created resumes the run", () => {
    const parked = reduceEvent(initial,
      ev({ type: EventType.ClarifyRequired, question: "Which project?" }) as RunEvent);
    const resumed = reduceEvent(parked,
      ev({ type: EventType.PlanCreated, step_count: 2 }) as RunEvent);
    expect(resumed.pendingClarify).toBeNull();
    expect(resumed.partialAnswer).toBe("");
  });

  it("clears pending clarify when answer deltas stream", () => {
    const parked = reduceEvent(initial,
      ev({ type: EventType.ClarifyRequired, question: "Which project?" }) as RunEvent);
    const streaming = reduceEvent(parked,
      ev({ type: EventType.AnswerDelta, text: "PLAT bugs", done: false }) as RunEvent);
    expect(streaming.pendingClarify).toBeNull();
    expect(streaming.partialAnswer).toBe("PLAT bugs");
  });

  it("captures pending approval", () => {
    const next = reduceEvent(initial,
      ev({ type: EventType.ApprovalRequired, pending_tool: "jira_create_issue" }) as RunEvent);
    expect(next.pendingApproval).toBe("jira_create_issue");
  });

  it("accumulates streamed answer deltas", () => {
    const first = reduceEvent(initial,
      ev({ type: EventType.AnswerDelta, text: "Hel", done: false }) as RunEvent);
    const next = reduceEvent(first,
      ev({ type: EventType.AnswerDelta, text: "lo", done: true }) as RunEvent);
    expect(next.partialAnswer).toBe("Hello");
    expect(next.status).toBe("streaming");
  });

  it("transitions to completed on run_finished", () => {
    const next = reduceEvent(initial, ev({ type: EventType.RunFinished }) as RunEvent);
    expect(next.status).toBe("completed");
    expect(next.pendingApproval).toBeNull();
  });

  it("transitions to failed on run_failed", () => {
    const next = reduceEvent(initial, ev({ type: EventType.RunFailed }) as RunEvent);
    expect(next.status).toBe("failed");
  });
});

describe("isTerminal", () => {
  it("marks finished/failed as terminal", () => {
    expect(isTerminal(ev({ type: EventType.RunFinished }) as RunEvent)).toBe(true);
    expect(isTerminal(ev({ type: EventType.RunFailed }) as RunEvent)).toBe(true);
    expect(isTerminal(ev({ type: EventType.NodeStarted }) as RunEvent)).toBe(false);
  });
});

describe("replayState", () => {
  it("keeps a clarify-parked replay non-terminal", () => {
    const clarify = ev({
      type: EventType.ClarifyRequired,
      question: "Which project?",
    }) as RunEvent;
    const state = replayState([clarify]);
    expect(state.status).toBe("streaming");
    expect(state.pendingClarify).toBe("Which project?");
  });

  it("drops pending clarify after resume events in replay", () => {
    const events = [
      ev({ type: EventType.ClarifyRequired, question: "Which project?" }) as RunEvent,
      ev({ type: EventType.PlanCreated, step_count: 4 }) as RunEvent,
      ev({ type: EventType.AgentSpawned, step_id: "s1", agent: "jira-analyst" }) as RunEvent,
    ];
    expect(clarifyResolved(events)).toBe(true);
    const state = replayState(events);
    expect(state.pendingClarify).toBeNull();
  });

  it("keeps an approval-parked replay non-terminal", () => {
    const approval = ev({
      type: EventType.ApprovalRequired,
      pending_tool: "jira_create_issue",
    }) as RunEvent;
    const state = replayState([approval]);
    expect(state.status).toBe("streaming");
    expect(state.pendingApproval).toBe("jira_create_issue");
  });

  it("hydrates failed and completed terminal states", () => {
    expect(replayState([ev({ type: EventType.RunFailed }) as RunEvent]).status).toBe("failed");
    expect(replayState([ev({ type: EventType.RunFinished }) as RunEvent]).status).toBe("completed");
  });

  it("keeps a partial trace streaming so replay can poll for completion", () => {
    expect(replayState([ev({ type: EventType.RunStarted }) as RunEvent]).status).toBe("streaming");
  });

  it("stays completed when memory embeddings arrive after run_finished", () => {
    const state = replayState([
      ev({ type: EventType.RunStarted, seq: 0 }) as RunEvent,
      ev({ type: EventType.RunFinished, seq: 1, result: "done" }) as RunEvent,
      ev({ type: EventType.EmbeddingUsage, seq: 2, node: "memory.write" }) as RunEvent,
    ]);
    expect(state.status).toBe("completed");
    expect(state.partialAnswer).toBe("done");
  });
});

describe("parseSse CRLF framing (sse-starlette wire format)", () => {
  it("parses CRLF-terminated frames", () => {
    const state = { buffer: "" };
    const events = parseSse(
      `data: {"type":"run_started","run_id":"r","ts":"t","seq":0,"goal":"g"}\r\n\r\n`,
      state,
    );
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe(EventType.RunStarted);
  });

  it("parses multiple CRLF frames in one chunk", () => {
    const state = { buffer: "" };
    const events = parseSse(
      `data: {"type":"run_started","run_id":"r","ts":"t","seq":0,"goal":"g"}\r\n\r\n` +
      `data: {"type":"node_started","run_id":"r","ts":"t","seq":1,"node":"s1"}\r\n\r\n`,
      state,
    );
    expect(events).toHaveLength(2);
    expect(events[1].type).toBe(EventType.NodeStarted);
  });

  it("reassembles a chunk boundary that splits the CRLF sequence", () => {
    const state = { buffer: "" };
    expect(parseSse(
      `data: {"type":"run_started","run_id":"r","ts":"t","seq":0,"goal":"g"}\r`, state,
    )).toHaveLength(0);
    const events = parseSse(
      `\ndata: {"type":"node_started","run_id":"r","ts":"t","seq":1,"node":"s1"}\r\n\r\n`,
      state,
    );
    expect(events).toHaveLength(2);
  });

  it("skips ping comments between CRLF frames", () => {
    const state = { buffer: "" };
    const events = parseSse(
      `: ping\r\n\r\n` +
      `data: {"type":"run_finished","run_id":"r","ts":"t","seq":2,"result":"ok","totals":{}}\r\n\r\n`,
      state,
    );
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe(EventType.RunFinished);
  });
});

describe("aggregateUsage", () => {
  const usage = (p: number, c: number, cost: number): RunEvent =>
    ev({ type: EventType.TokenUsage, node: "s1", prompt: p,
         completion: c, cost_usd: cost } as unknown as RunEvent);

  it("sums tokens, calls and cost across events", () => {
    const totals = aggregateUsage([
      usage(100, 50, 0.001),
      usage(200, 25, 0.002),
      ev({ type: EventType.NodeStarted, node: "s1" }) as RunEvent,
    ]);
    expect(totals.prompt).toBe(300);
    expect(totals.completion).toBe(75);
    expect(totals.tokens).toBe(375);
    expect(totals.calls).toBe(2);
    expect(totals.costUsd).toBeCloseTo(0.003, 6);
  });

  it("returns zeros for an empty run", () => {
    expect(aggregateUsage([]).tokens).toBe(0);
  });

  it("falls back to run_finished totals when usage events are missing", () => {
    const totals = aggregateUsage([
      ev({ type: EventType.RunFinished, result: "ok",
           totals: { tokens: 240, prompt: 180, completion: 60, cost_usd: 0.002, calls: 3 } }),
    ]);
    expect(totals.tokens).toBe(240);
    expect(totals.calls).toBe(3);
    expect(totals.costUsd).toBeCloseTo(0.002, 6);
  });
});

describe("traceStateEqual", () => {
  const base: TraceState = {
    events: [ev({ type: EventType.RunStarted, goal: "hi" })],
    status: "streaming",
    pendingApproval: null,
    pendingClarify: "Which project?",
    partialAnswer: "Which project?",
  };

  it("detects unchanged trace snapshots", () => {
    expect(traceStateEqual(base, { ...base, events: [...base.events] })).toBe(true);
  });

  it("detects pending clarify changes", () => {
    expect(traceStateEqual(base, { ...base, pendingClarify: "Other?" })).toBe(false);
  });
});
