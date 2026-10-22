/** Execution DAG for the Workflow inspector — full run, not just planner steps. */

import { EventType, planSteps, type PlanStep, type RunEvent } from "./events";
import { truncate } from "./format";
import type { RunDetail } from "./types";

export interface PlanShape {
  steps: { id: string; agent: string; task: string; tools?: string[]; depends_on?: string[] }[];
  synthesis: string;
}

export type WfKind = "goal" | "router" | "clarify" | "approval" | "plan" | "agent" | "tool" | "embed" | "answer";
export type WfStatus = "pending" | "running" | "done" | "failed";
export type WfConcurrency = "parallel" | "serial";
export type WfEdgeKind = "flow" | "parallel" | "serial";

export interface WfField {
  label: string;
  value: string;
}

export interface WfContextSection {
  key: string;
  title: string;
  content: string;
  format: "text" | "json";
}

export interface WfNodeSpec {
  id: string;
  kind: WfKind;
  label: string;
  sub: string;
  status: WfStatus;
  layer: number;
  slot: number;
  concurrency?: WfConcurrency;
  fields?: WfField[];
  /** Legacy combined detail — prefer input/output when set. */
  body?: string;
  /** What this step received (goal, task, args, plan context). */
  input?: string;
  /** What this step produced (summary, digest, answer). */
  output?: string;
  /** Memory, bindings, and prompts passed into this step (from trace). */
  contextSections?: WfContextSection[];
  resultId?: string;
  /** Node shows the detail inspector popup. */
  inspectable?: boolean;
}

export interface WfEdgeSpec {
  id: string;
  source: string;
  target: string;
  kind: WfEdgeKind;
  label?: string;
}

export interface WorkflowModel {
  nodes: WfNodeSpec[];
  edges: WfEdgeSpec[];
  path: string;
  agentCount: number;
  toolCount: number;
  parallelAgents: boolean;
  parallelTools: boolean;
}

const OVERLAP_MS = 8;

interface ToolSpan {
  id: string;
  stepId: string;
  tool: string;
  start: number;
  end: number;
  status: WfStatus;
  preview: string;
  seq: number;
  mode?: "parallel" | "serial";
  ms?: number;
  resultId?: string;
  args?: Record<string, unknown>;
}

function at(iso: string, fallback = 0): number {
  const value = Date.parse(iso);
  return Number.isNaN(value) ? fallback : value;
}

function overlaps(a: ToolSpan, b: ToolSpan): boolean {
  const shared = Math.min(a.end, b.end) - Math.max(a.start, b.start);
  return shared >= OVERLAP_MS;
}

function prettyTool(name: string): string {
  return name.replaceAll("_", " ");
}

function collectTools(events: RunEvent[]): ToolSpan[] {
  const pending = new Map<string, Extract<RunEvent, { type: EventType.ToolCallStarted }>[]>();
  const out: ToolSpan[] = [];
  let seq = 0;
  for (const event of events) {
    if (event.type === EventType.ToolCallStarted) {
      const key = `${event.step_id}:${event.tool}`;
      const queue = pending.get(key) ?? [];
      queue.push(event);
      pending.set(key, queue);
    }
    if (event.type === EventType.ToolCallFinished) {
      const key = `${event.step_id}:${event.tool}`;
      const queue = pending.get(key) ?? [];
      const startEv = queue.shift();
      const start = startEv ? at(startEv.ts) : Math.max(0, at(event.ts) - (event.ms || 0));
      const end = event.ms > 0 ? start + event.ms : at(event.ts);
      const mode = event.mode === "parallel" || event.mode === "serial"
        ? event.mode
        : (startEv && "mode" in startEv && (startEv.mode === "parallel" || startEv.mode === "serial")
          ? startEv.mode
          : undefined);
      out.push({
        id: `tool-${event.step_id}-${event.seq}`,
        stepId: event.step_id,
        tool: event.tool,
        start,
        end: Math.max(start, end),
        status: event.status === "ok" ? "done" : "failed",
        preview: event.digest_preview || prettyTool(event.tool),
        seq: event.seq,
        mode,
        ms: event.ms > 0 ? event.ms : Math.max(0, end - start),
        resultId: event.result_id ?? undefined,
        args: startEv && "args_digest" in startEv ? startEv.args_digest : undefined,
      });
    }
  }
  for (const queue of pending.values()) {
    for (const startEv of queue) {
      seq += 1;
      const start = at(startEv.ts);
      out.push({
        id: `tool-${startEv.step_id}-live-${startEv.seq}`,
        stepId: startEv.step_id,
        tool: startEv.tool,
        start,
        end: start,
        status: "running",
        preview: prettyTool(startEv.tool),
        seq: startEv.seq + seq,
        args: "args_digest" in startEv ? startEv.args_digest : undefined,
      });
    }
  }
  return out.sort((a, b) => a.start - b.start || a.seq - b.seq);
}

function wavesFor(tools: ToolSpan[]): ToolSpan[][] {
  const waves: ToolSpan[][] = [];
  for (const tool of tools) {
    const last = waves.at(-1);
    const plannedParallel = tool.mode === "parallel"
      && last?.every((other) => other.mode === "parallel" && other.stepId === tool.stepId);
    if (plannedParallel || last?.some((other) => overlaps(other, tool))) last?.push(tool);
    else waves.push([tool]);
  }
  return waves;
}

function markStatus(
  current: WfStatus,
  next: WfStatus,
): WfStatus {
  const rank: Record<WfStatus, number> = { pending: 0, running: 1, done: 2, failed: 3 };
  return rank[next] >= rank[current] ? next : current;
}

export function buildWorkflowModel(
  plan: PlanShape | null,
  events: RunEvent[],
  runDetail: RunDetail | null,
): WorkflowModel {
  if (!plan && events.length === 0 && !runDetail) {
    return {
      nodes: [], edges: [], path: "", agentCount: 0, toolCount: 0,
      parallelAgents: false, parallelTools: false,
    };
  }

  const runFinished = events.some((e) => e.type === EventType.RunFinished);
  const runFailed = events.some((e) => e.type === EventType.RunFailed);
  const routerEv = [...events].reverse().find((e) => e.type === EventType.RouterDecision);
  const clarifyEv = [...events].reverse().find((e) => e.type === EventType.ClarifyRequired);
  const planEv = [...events].reverse().find((e) => e.type === EventType.PlanCreated);
  const started = events.find((e) => e.type === EventType.RunStarted);
  const retrieveEmbeds = events.filter((e) =>
    e.type === EventType.EmbeddingUsage && e.node.startsWith("memory.retrieve"));
  const tools = collectTools(events);
  const contextByNode = collectContextSnapshots(events);

  const steps = planSteps(
    (plan?.steps ?? (planEv && "plan" in planEv ? planEv.plan.steps : undefined)) as PlanStep[] | undefined,
  );
  const ORCHESTRATION_NODES = new Set(["router", "planner", "synthesis", "aggregate"]);
  const extraAgents = new Map<string, { agent: string; task: string }>();
  for (const event of events) {
    if (event.type === EventType.AgentSpawned && event.step_id !== "synthesis") {
      extraAgents.set(event.step_id, { agent: event.agent, task: event.agent });
    }
    if (event.type === EventType.NodeStarted && !ORCHESTRATION_NODES.has(event.node)
        && !event.node.startsWith("memory")) {
      if (!extraAgents.has(event.node) && !steps.some((s) => s.id === event.node)) {
        extraAgents.set(event.node, { agent: event.node, task: event.node });
      }
    }
  }
  const agentDefs = [
    ...steps.map((s) => ({
      id: s.id,
      agent: s.agent,
      task: s.task,
      depends_on: s.depends_on ?? [],
    })),
    ...[...extraAgents.entries()]
      .filter(([id]) => !steps.some((s) => s.id === id))
      .map(([id, meta]) => ({ id, agent: meta.agent, task: meta.task, depends_on: [] as string[] })),
  ];

  const agentStatus: Record<string, { st: WfStatus; summary: string }> = {};
  for (const step of agentDefs) {
    agentStatus[step.id] = { st: "pending", summary: step.task };
  }
  let synthesisSt: WfStatus = "pending";
  let synthesisSummary = plan?.synthesis
    || (planEv && "plan" in planEv ? planEv.plan.synthesis : "")
    || "Synthesize the final answer";
  let routerSt: WfStatus = routerEv ? "done" : events.length ? "running" : "pending";
  let plannerSt: WfStatus = plan || planEv ? "done" : "pending";

  for (const event of events) {
    if (event.type === EventType.NodeStarted) {
      if (event.node === "synthesis" || event.node === "aggregate") {
        synthesisSt = markStatus(synthesisSt, "running");
      } else if (agentStatus[event.node]) {
        agentStatus[event.node].st = markStatus(agentStatus[event.node].st, "running");
      }
    }
    if (event.type === EventType.NodeFinished) {
      if (event.node === "synthesis" || event.node === "aggregate") {
        synthesisSt = "done";
        if (event.summary) synthesisSummary = event.summary;
      } else if (agentStatus[event.node]) {
        agentStatus[event.node].st = "done";
        if (event.summary) agentStatus[event.node].summary = event.summary;
      }
    }
    if (event.type === EventType.PlanCreated) plannerSt = "done";
    if (event.type === EventType.RouterDecision) routerSt = "done";
  }
  if (runFailed) {
    for (const step of agentDefs) {
      if (agentStatus[step.id].st === "running") agentStatus[step.id].st = "failed";
    }
    if (synthesisSt === "running" || synthesisSt === "pending") synthesisSt = "failed";
  } else if (runFinished) {
    for (const step of agentDefs) {
      if (agentStatus[step.id].st !== "failed") agentStatus[step.id].st = "done";
    }
    if (synthesisSt !== "failed") synthesisSt = "done";
    plannerSt = plan || planEv ? "done" : plannerSt;
    routerSt = "done";
  }

  const specs: Omit<WfNodeSpec, "layer" | "slot">[] = [];
  const rawEdges: Omit<WfEdgeSpec, "id">[] = [];
  const link = (source: string, target: string, kind: WfEdgeKind = "flow", label?: string) => {
    rawEdges.push({ source, target, kind, label });
  };

  const goalText = runDetail?.goal || (started && "goal" in started ? started.goal : "User request");
  specs.push({
    id: "goal", kind: "goal", label: "goal",
    sub: truncate(goalText, 140), status: "done",
    inspectable: true,
    input: String(goalText),
    output: runDetail?.result ? truncate(String(runDetail.result), 400) : "Routed to orchestration",
    fields: [
      { label: "Kind", value: "User goal" },
      { label: "Status", value: "Done" },
    ],
    body: String(goalText),
  });

  let cursor = "goal";
  const retrieveLast = retrieveEmbeds.at(-1);
  if (retrieveLast) {
    const embedSub = retrieveLast.type === EventType.EmbeddingUsage
      ? `${retrieveLast.model || retrieveLast.provider} · ${retrieveLast.cached ? "cached" : "live"}`
      : "episodic hint";
    specs.push({
      id: "embed-retrieve",
      kind: "embed",
      label: "memory retrieve",
      sub: embedSub,
      status: "done",
      inspectable: true,
      input: "User goal (embedding query)",
      output: embedSub,
      fields: [
        { label: "Kind", value: "Memory retrieve" },
        { label: "Status", value: "Done" },
        ...(retrieveLast.type === EventType.EmbeddingUsage ? [
          { label: "Provider", value: String(retrieveLast.provider || "voyage") },
          { label: "Model", value: String(retrieveLast.model || "—") },
          { label: "Cache", value: retrieveLast.cached ? "Cached" : "Live" },
        ] : []),
      ],
      body: embedSub,
    });
    link(cursor, "embed-retrieve");
    cursor = "embed-retrieve";
  }

  const showRouter = Boolean(routerEv || events.length > 0 || runDetail);
  if (showRouter) {
    const routerSub = routerEv && "path" in routerEv
      ? `${routerEv.intent} → ${routerEv.path}${routerEv.reason ? ` · ${routerEv.reason}` : ""}`
      : "Classify the request";
    specs.push({
      id: "router",
      kind: "router",
      label: "router",
      sub: routerSub,
      status: routerSt,
      inspectable: true,
      input: String(goalText),
      output: routerSub,
      fields: [
        { label: "Kind", value: "Router" },
        { label: "Status", value: statusLabel(routerSt) },
        ...(routerEv && "path" in routerEv ? [
          { label: "Intent", value: String(routerEv.intent) },
          { label: "Path", value: String(routerEv.path) },
          ...(routerEv.reason ? [{ label: "Reason", value: String(routerEv.reason) }] : []),
        ] : []),
      ],
      body: routerSub,
    });
    link(cursor, "router");
    cursor = "router";
  }

  const showClarify = Boolean(clarifyEv);
  if (showClarify && clarifyEv && clarifyEv.type === EventType.ClarifyRequired) {
    const clarificationTail = String(goalText).includes("User clarification:")
      ? String(goalText).split("User clarification:")[1]?.trim() ?? ""
      : "";
    const clarifyOutput = [clarifyEv.question, clarificationTail].filter(Boolean).join("\n");
    const clarifySub = truncate(clarifyEv.question, 72);
    specs.push({
      id: "clarify",
      kind: "clarify",
      label: "clarify",
      sub: clarifySub,
      status: planEv || runFinished ? "done" : "running",
      inspectable: true,
      input: String(goalText),
      output: clarifyOutput,
      fields: [
        { label: "Kind", value: "Human clarify" },
        { label: "Question", value: clarifyEv.question },
        ...(clarificationTail ? [{ label: "Resolution", value: clarificationTail }] : []),
      ],
      body: clarifyOutput,
    });
    link(cursor, "clarify");
    cursor = "clarify";
  }

  const showPlanner = Boolean(plan || planEv || agentDefs.length > 0);
  if (showPlanner) {
    const plannerStatus = plannerSt === "pending" && (routerSt === "done" || cursor === "router")
      ? (runFinished ? "done" : "running")
      : plannerSt;
    const plannerBody = agentDefs.map((s) => `${s.agent}: ${s.task}`).join("\n")
      || (plan?.synthesis ?? "Compose a multi-agent plan");
    specs.push({
      id: "planner",
      kind: "plan",
      label: "planner",
      sub: `${agentDefs.length} agent${agentDefs.length === 1 ? "" : "s"}`,
      status: plannerStatus,
      inspectable: true,
      input: [
        `Goal: ${goalText}`,
        routerEv && "path" in routerEv ? `Router path: ${routerEv.path}` : "",
      ].filter(Boolean).join("\n"),
      output: plannerBody,
      fields: [
        { label: "Kind", value: "Planner" },
        { label: "Status", value: statusLabel(plannerStatus) },
        { label: "Agents", value: agentDefs.map((s) => s.agent).join(", ") || "—" },
        ...(plan?.synthesis ? [{ label: "Synthesis", value: plan.synthesis }] : []),
      ],
      body: plannerBody,
    });
    link(cursor, "planner");
    cursor = "planner";
  }

  inferMissingDependsOn(agentDefs, events);

  const toolsByStep = new Map<string, ToolSpan[]>();
  for (const tool of tools) {
    const owner = agentDefs.some((s) => s.id === tool.stepId) ? tool.stepId : (agentDefs[0]?.id ?? tool.stepId);
    const list = toolsByStep.get(owner) ?? [];
    list.push({ ...tool, stepId: owner });
    toolsByStep.set(owner, list);
  }

  const roots = agentDefs.filter((s) => s.depends_on.length === 0);
  const parallelAgents = roots.length > 1;
  let parallelTools = false;
  const lastOut = new Map<string, string[]>();
  const toolWaves = new Map<string, ToolSpan[][]>();
  const spawnedByStep = new Map<string, Extract<RunEvent, { type: EventType.AgentSpawned }>>();
  for (const event of events) {
    if (event.type === EventType.AgentSpawned) {
      spawnedByStep.set(event.step_id, event);
    }
  }

  for (const step of agentDefs) {
    const waves = wavesFor(toolsByStep.get(step.id) ?? []);
    toolWaves.set(step.id, waves);
    lastOut.set(step.id, [step.id]);
    const agentSt = agentStatus[step.id]?.st ?? "pending";
    const agentSummary = agentStatus[step.id]?.summary || step.task;
    const spawned = spawnedByStep.get(step.id);
    const stepTools = toolsByStep.get(step.id) ?? [];
    const toolOutput = stepTools.length
      ? stepTools.map((t) => `${t.tool}: ${t.preview}`).join("\n")
      : "";
    const agentInput = [
      `Task: ${step.task}`,
      formatAgentToolsLine(
        spawned,
        contextByNode.get(step.id),
        steps.find((s) => s.id === step.id)?.tools ?? [],
      ),
      step.depends_on.length ? `Depends on: ${step.depends_on.join(", ")}` : "",
    ].filter(Boolean).join("\n");
    const agentOutput = [agentSummary, toolOutput ? `Tool results:\n${toolOutput}` : ""]
      .filter(Boolean)
      .join("\n\n");
    specs.push({
      id: step.id,
      kind: "agent",
      label: step.agent,
      sub: truncate(agentSummary, 140),
      status: agentSt,
      inspectable: true,
      input: agentInput,
      output: agentOutput,
      concurrency: parallelAgents && step.depends_on.length === 0 ? "parallel" : undefined,
      fields: [
        { label: "Kind", value: "Agent" },
        { label: "Status", value: statusLabel(agentSt) },
        { label: "Step", value: step.id },
        { label: "Agent", value: step.agent },
        { label: "Task", value: step.task },
        ...(step.depends_on.length ? [{ label: "Depends on", value: step.depends_on.join(", ") }] : []),
      ],
      body: agentOutput || agentSummary,
    });
    waves.forEach((wave, waveIndex) => {
      const parallel = wave.length > 1;
      if (parallel) parallelTools = true;
      const waveIds: string[] = [];
      const prevIds = waveIndex === 0 ? [step.id] : waves[waveIndex - 1].map((t) => t.id);
      for (const tool of wave) {
        const duration = tool.ms ?? Math.max(0, tool.end - tool.start);
        const callMode = parallel ? "Parallel"
          : (tool.mode === "serial" || waveIndex > 0 ? "Sequential" : "Single");
        const argText = formatArgs(tool.args);
        specs.push({
          id: tool.id,
          kind: "tool",
          label: prettyTool(tool.tool),
          sub: truncate(tool.preview, 120),
          status: tool.status,
          inspectable: true,
          input: argText || `Tool: ${tool.tool}`,
          output: tool.preview || (tool.status === "failed" ? "Call failed" : "—"),
          concurrency: parallel ? "parallel" : (waveIndex > 0 ? "serial" : undefined),
          fields: [
            { label: "Kind", value: "Tool" },
            { label: "Status", value: statusLabel(tool.status) },
            { label: "Tool name", value: tool.tool },
            { label: "Agent step", value: step.id },
            { label: "Call mode", value: callMode },
            ...(duration ? [{ label: "Duration", value: `${duration}ms` }] : []),
            ...argFields(tool.args),
          ],
          body: tool.preview,
          resultId: tool.resultId,
        });
        for (const src of prevIds) {
          link(src, tool.id, parallel ? "parallel" : "serial",
            parallel ? "parallel" : (waveIndex > 0 ? "then" : undefined));
        }
        waveIds.push(tool.id);
      }
      lastOut.set(step.id, waveIds);
    });
  }

  for (const step of agentDefs) {
    if (step.depends_on.length === 0) {
      link(cursor, step.id, parallelAgents ? "parallel" : "flow",
        parallelAgents ? "parallel" : undefined);
      continue;
    }
    for (const dep of step.depends_on) {
      for (const src of lastOut.get(dep) ?? [dep]) {
        link(src, step.id, "serial", src === dep ? undefined : "then");
      }
    }
  }

  const showSynthesis = Boolean(plan || planEv || agentDefs.length > 0
    || events.some((e) =>
      (e.type === EventType.NodeStarted || e.type === EventType.NodeFinished)
      && (e.node === "synthesis" || e.node === "aggregate")));
  const answerText = runDetail?.result
    || ([...events].reverse().find((e) => e.type === EventType.RunFinished) as { result?: string } | undefined)?.result
    || synthesisSummary;
  let sinkId: string | null = null;

  if (showSynthesis) {
    sinkId = "synthesis";
    specs.push({
      id: "synthesis",
      kind: "answer",
      label: "synthesis",
      sub: truncate(answerText, 160),
      status: synthesisSt,
      inspectable: true,
      input: plan?.synthesis
        || (planEv && "plan" in planEv ? planEv.plan.synthesis : "")
        || synthesisSummary,
      output: answerText || synthesisSummary,
      fields: [
        { label: "Kind", value: "Synthesis" },
        { label: "Status", value: statusLabel(synthesisSt) },
      ],
      body: answerText || "Synthesize the final answer",
    });
    const leaves = agentDefs.filter((s) =>
      !agentDefs.some((other) => other.depends_on.includes(s.id)));
    const sources = leaves.length > 0 ? leaves : agentDefs;
    for (const step of sources) {
      for (const src of lastOut.get(step.id) ?? [step.id]) {
        link(src, "synthesis");
      }
    }
    if (sources.length === 0) link(cursor, "synthesis");
  } else if (runFinished || events.some((e) => e.type === EventType.AnswerDelta)) {
    sinkId = "answer";
    const answerSt: WfStatus = runFailed ? "failed" : runFinished ? "done" : "running";
    specs.push({
      id: "answer",
      kind: "answer",
      label: "answer",
      sub: truncate(answerText || "Direct reply", 160),
      status: answerSt,
      inspectable: true,
      input: String(goalText),
      output: answerText || "Direct reply",
      fields: [
        { label: "Kind", value: "Answer" },
        { label: "Status", value: statusLabel(answerSt) },
      ],
      body: answerText || "Direct reply",
    });
    link(cursor, "answer");
  }

  const ids = new Set(specs.map((n) => n.id));
  const edges: WfEdgeSpec[] = rawEdges
    .filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target)
    .map((e, i) => ({ id: `${e.source}->${e.target}-${i}`, ...e }));

  const placed = assignColumnLayout({
    spine: ["goal", retrieveLast ? "embed-retrieve" : null, showRouter ? "router" : null,
      showClarify ? "clarify" : null, showPlanner ? "planner" : null].filter((id): id is string => Boolean(id)),
    agents: agentDefs,
    toolWaves,
    sinkId,
  });

  const nodes: WfNodeSpec[] = specs.map((spec) => ({
    ...spec,
    layer: placed.get(spec.id)?.layer ?? 0,
    slot: placed.get(spec.id)?.slot ?? 0,
    contextSections: contextByNode.get(spec.id),
  }));

  const path = [
    "goal",
    showRouter ? "router" : null,
    showClarify ? "clarify" : null,
    showPlanner ? "planner" : null,
    ...agentDefs.map((s) => s.agent),
    showSynthesis ? "synthesis" : null,
  ].filter(Boolean).join(" → ");

  return {
    nodes,
    edges,
    path,
    agentCount: agentDefs.length,
    toolCount: tools.length,
    parallelAgents,
    parallelTools,
  };
}

interface AgentDef {
  id: string;
  agent: string;
  task: string;
  depends_on: string[];
}

function inferMissingDependsOn(agents: AgentDef[], events: RunEvent[]): void {
  if (agents.some((a) => a.depends_on.length > 0)) return;
  const started = new Map<string, number>();
  const finished = new Map<string, number>();
  for (const event of events) {
    if (event.type === EventType.NodeStarted) started.set(event.node, at(event.ts));
    if (event.type === EventType.NodeFinished) finished.set(event.node, at(event.ts));
  }
  for (let i = 1; i < agents.length; i += 1) {
    const prev = agents[i - 1];
    const cur = agents[i];
    const prevEnd = finished.get(prev.id);
    const curStart = started.get(cur.id);
    if (prevEnd != null && curStart != null && curStart >= prevEnd) {
      cur.depends_on = [prev.id];
    }
  }
}

function agentDepthOf(id: string, agents: AgentDef[], memo: Map<string, number>): number {
  const hit = memo.get(id);
  if (hit != null) return hit;
  const step = agents.find((a) => a.id === id);
  const deps = step?.depends_on.filter((dep) => agents.some((a) => a.id === dep)) ?? [];
  const depth = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((dep) => agentDepthOf(dep, agents, memo)));
  memo.set(id, depth);
  return depth;
}

function assignColumnLayout(input: {
  spine: string[];
  agents: AgentDef[];
  toolWaves: Map<string, ToolSpan[][]>;
  sinkId: string | null;
}): Map<string, { layer: number; slot: number }> {
  const { spine, agents, toolWaves, sinkId } = input;
  const depths = new Map<string, number>();
  agents.forEach((a) => agentDepthOf(a.id, agents, depths));
  const bands = new Map<number, AgentDef[]>();
  for (const agent of agents) {
    const depth = depths.get(agent.id) ?? 0;
    const band = bands.get(depth) ?? [];
    band.push(agent);
    bands.set(depth, band);
  }
  const maxCols = Math.max(1, ...[...bands.values()].map((band) => band.length));
  const spineSlot = (maxCols - 1) / 2;
  const placed = new Map<string, { layer: number; slot: number }>();
  let layer = 0;
  for (const id of spine) {
    placed.set(id, { layer, slot: spineSlot });
    layer += 1;
  }
  const bandKeys = [...bands.keys()].sort((a, b) => a - b);
  for (const key of bandKeys) {
    const band = bands.get(key) ?? [];
    const bandStart = layer;
    let tallest = 1;
    band.forEach((agent, index) => {
      const slot = band.length === 1 ? spineSlot : index;
      placed.set(agent.id, { layer: bandStart, slot });
      const waves = toolWaves.get(agent.id) ?? [];
      waves.forEach((wave, waveIndex) => {
        const toolLayer = bandStart + 1 + waveIndex;
        tallest = Math.max(tallest, 1 + waveIndex + 1);
        wave.forEach((tool, toolIndex) => {
          const offset = wave.length === 1 ? 0 : toolIndex - (wave.length - 1) / 2;
          placed.set(tool.id, { layer: toolLayer, slot: slot + offset });
        });
      });
    });
    layer = bandStart + tallest;
  }
  if (sinkId) placed.set(sinkId, { layer, slot: spineSlot });
  return placed;
}

export function workflowPositions(
  nodes: WfNodeSpec[],
  xGap = 300,
  yGap = 156,
): Record<string, { x: number; y: number }> {
  const slots = nodes.map((node) => node.slot);
  const mid = slots.length ? (Math.min(...slots) + Math.max(...slots)) / 2 : 0;
  const out: Record<string, { x: number; y: number }> = {};
  for (const node of nodes) {
    out[node.id] = {
      x: (node.slot - mid) * xGap,
      y: node.layer * yGap,
    };
  }
  return out;
}

function statusLabel(status: WfStatus): string {
  if (status === "done") return "Done";
  if (status === "running") return "Running";
  if (status === "failed") return "Failed";
  return "Pending";
}

function argFields(args?: Record<string, unknown>): WfField[] {
  if (!args) return [];
  return Object.entries(args)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 10)
    .map(([label, value]) => ({
      label,
      value: typeof value === "string" ? value : JSON.stringify(value),
    }));
}

function formatAgentToolsLine(
  spawned: Extract<RunEvent, { type: EventType.AgentSpawned }> | undefined,
  contextSections: WfContextSection[] | undefined,
  plannedTools: string[],
): string {
  const bindingsSection = contextSections?.find((s) => s.key === "bindings");
  if (bindingsSection?.format === "json") {
    try {
      const bindings = JSON.parse(bindingsSection.content) as {
        tools_registered?: number;
        tools_offered?: string[];
        tools_allowlist_count?: number;
        tool_execution?: string;
        llm_tool_binding?: string;
        tool_mode?: string;
      };
      const exec = bindings.tool_execution || "mcp";
      const binding = bindings.llm_tool_binding
        || (bindings.tool_mode === "native_api" ? "openai_tools_api" : bindings.tool_mode)
        || "openai_tools_api";
      const count = bindings.tools_registered ?? bindings.tools_offered?.length ?? 0;
      if (count > 0) {
        const lines = [
          `Tool execution: ${exec} (MCP server)`,
          `LLM tool binding: ${binding} (${count} schema${count === 1 ? "" : "s"} registered)`,
        ];
        const allowCount = bindings.tools_allowlist_count ?? spawned?.tools.length ?? 0;
        if (allowCount > count) {
          lines.push(`Agent allowlist (YAML): ${allowCount} tools bound at spawn`);
        }
        return lines.join("\n");
      }
      if (bindings.tools_offered?.length) {
        const mode = bindings.tool_mode === "native_api" ? "openai_tools_api" : bindings.tool_mode ?? "API";
        const lines = [`Tools offered to LLM (${mode}): ${bindings.tools_offered.join(", ")}`];
        const allowCount = bindings.tools_allowlist_count ?? spawned?.tools.length ?? 0;
        if (allowCount > bindings.tools_offered.length) {
          lines.push(`Agent allowlist (YAML): ${allowCount} tools bound at spawn`);
        }
        return lines.join("\n");
      }
    } catch {
      /* fall through to spawn metadata */
    }
  }
  if (spawned) {
    return `Agent allowlist (YAML): ${spawned.tools.join(", ") || "—"}`;
  }
  return `Planned tools: ${plannedTools.join(", ") || "—"}`;
}

function formatArgs(args?: Record<string, unknown>): string {
  if (!args || Object.keys(args).length === 0) return "";
  return JSON.stringify(args, null, 2);
}

function collectContextSnapshots(events: RunEvent[]): Map<string, WfContextSection[]> {
  const map = new Map<string, WfContextSection[]>();
  for (const event of events) {
    if (event.type === EventType.ContextSnapshot && event.sections?.length) {
      map.set(event.node, event.sections);
    }
  }
  return map;
}
