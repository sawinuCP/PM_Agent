/** Plain-language run progress derived from the SSE event stream. */

import { clarifyResolved } from "./client";
import { EventType, planSteps, type PlanStep, type RunEvent } from "./events";

export interface RunProgress {
  title: string;
  detail: string;
  phase: "start" | "route" | "plan" | "execute" | "synthesize" | "write" | "clarify" | "failed";
}

const NODE_LABELS: Record<string, string> = {
  router: "Router",
  planner: "Planner",
  synthesis: "Synthesis",
  fast: "Direct answer",
};

function friendlyAgent(agent: string): string {
  return agent.replaceAll("-", " ");
}

function friendlyTool(tool: string): string {
  return tool.replaceAll("_", " ").replace(/^jira /i, "Jira ");
}

function stepById(steps: PlanStep[], id: string): PlanStep | undefined {
  return steps.find((step) => step.id === id);
}

function nodeFinished(events: RunEvent[], node: string, afterSeq: number): boolean {
  return events.some(
    (event) => event.type === EventType.NodeFinished
      && "node" in event
      && event.node === node
      && event.seq > afterSeq,
  );
}

function tokenUsageAfter(events: RunEvent[], seq: number, node: string): boolean {
  return events.some(
    (event) => event.type === EventType.TokenUsage
      && event.node === node
      && event.seq > seq,
  );
}

export function deriveRunProgress(events: RunEvent[]): RunProgress {
  if (events.length === 0) {
    return {
      title: "Working on your request",
      detail: "Sending your question to Azend…",
      phase: "start",
    };
  }

  const failed = [...events].reverse().find((event) => event.type === EventType.RunFailed);
  if (failed && failed.type === EventType.RunFailed) {
    return {
      title: "Something went wrong",
      detail: failed.error || "Open activity to review what happened.",
      phase: "failed",
    };
  }

  const pendingApproval = [...events].reverse().find((event) => event.type === EventType.ApprovalRequired);
  if (pendingApproval && pendingApproval.type === EventType.ApprovalRequired && !events.some((event) => event.type === EventType.RunFinished)) {
    return {
      title: "Waiting for your approval",
      detail: `Sensitive action: ${friendlyTool(pendingApproval.pending_tool)}`,
      phase: "execute",
    };
  }

  const pendingClarify = [...events].reverse().find((event) => event.type === EventType.ClarifyRequired);
  if (pendingClarify && pendingClarify.type === EventType.ClarifyRequired
      && !clarifyResolved(events)
      && !events.some((event) => event.type === EventType.RunFinished)) {
    return {
      title: "Needs your input",
      detail: pendingClarify.question,
      phase: "clarify",
    };
  }

  if (events.some((event) => event.type === EventType.AnswerDelta)) {
    return {
      title: "Writing your answer",
      detail: "Streaming the final reply into the chat…",
      phase: "write",
    };
  }

  const planEvent = [...events].reverse().find((event) => event.type === EventType.PlanCreated);
  const steps = planEvent && "plan" in planEvent ? planSteps(planEvent.plan.steps) : [];
  const finishedNodes = new Set(
    events.flatMap((event) => (
      event.type === EventType.NodeFinished && event.node !== "synthesis" ? [event.node] : []
    )),
  );

  const llmStarted = [...events].reverse().find((event) => event.type === EventType.LlmCallStarted);
  if (llmStarted && llmStarted.type === EventType.LlmCallStarted) {
    const stillRunning = !tokenUsageAfter(events, llmStarted.seq, llmStarted.node);
    if (stillRunning) {
      return {
        title: NODE_LABELS[llmStarted.node] ?? llmStarted.node,
        detail: llmStarted.label || "Calling the language model…",
        phase: llmStarted.node === "synthesis"
          ? "synthesize"
          : llmStarted.node === "planner"
            ? "plan"
            : "execute",
      };
    }
  }

  const activeNode = [...events].reverse().find((event) => event.type === EventType.NodeStarted);
  const activeNodeId = activeNode && "node" in activeNode ? activeNode.node : null;
  const activeNodeOpen = activeNodeId
    && !nodeFinished(events, activeNodeId, activeNode.seq);

  if (activeNodeOpen && activeNodeId === "synthesis") {
    return {
      title: "Final synthesis",
      detail: "Combining specialist results into your answer…",
      phase: "synthesize",
    };
  }

  const latestToolStart = [...events].reverse().find((event) => event.type === EventType.ToolCallStarted);
  const latestToolFinish = [...events].reverse().find((event) => event.type === EventType.ToolCallFinished);
  const toolInFlight = latestToolStart
    && (!latestToolFinish || latestToolFinish.seq < latestToolStart.seq);

  if (toolInFlight && latestToolStart.type === EventType.ToolCallStarted) {
    return {
      title: `Step ${latestToolStart.step_id}`,
      detail: `Fetching data via ${friendlyTool(latestToolStart.tool)}…`,
      phase: "execute",
    };
  }

  if (activeNodeOpen && activeNodeId && activeNodeId !== "synthesis") {
    const step = stepById(steps, activeNodeId);
    const agent = step?.agent ? friendlyAgent(step.agent) : activeNodeId;
    const taskHint = step?.task
      ? `${step.task.slice(0, 140)}${step.task.length > 140 ? "…" : ""}`
      : "";
    return {
      title: `${agent} · ${activeNodeId}`,
      detail: taskHint || `${agent} is gathering evidence and deciding next actions…`,
      phase: "execute",
    };
  }

  const route = [...events].reverse().find((event) => event.type === EventType.RouterDecision);
  if (route && route.type === EventType.RouterDecision && !planEvent) {
    if (route.path === "fast") {
      return {
        title: "Direct answer path",
        detail: "No multi-step plan needed — drafting a reply…",
        phase: "route",
      };
    }
    return {
      title: "Planning next",
      detail: route.reason || "Choosing specialists and tools for your question…",
      phase: "plan",
    };
  }

  if (planEvent && steps.length > 0 && finishedNodes.size < steps.length) {
    const nextStep = steps.find((step) => !finishedNodes.has(step.id));
    if (nextStep) {
      return {
        title: `Starting ${friendlyAgent(nextStep.agent)}`,
        detail: `${nextStep.task.slice(0, 140)}${nextStep.task.length > 140 ? "…" : ""}`,
        phase: "execute",
      };
    }
  }

  if (planEvent && steps.length > 0 && finishedNodes.size >= steps.length) {
    return {
      title: "Almost done",
      detail: "All specialist steps finished — preparing your answer…",
      phase: "synthesize",
    };
  }

  return {
    title: "Understanding your request",
    detail: "Reading session context and routing your question…",
    phase: "start",
  };
}
