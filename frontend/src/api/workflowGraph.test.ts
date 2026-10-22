import { describe, expect, it } from "vitest";

import { EventType, type RunEvent } from "./events";
import { buildWorkflowModel, workflowPositions } from "./workflowGraph";

const ev = (over: Partial<RunEvent> & { type: EventType; seq: number; ts: string }): RunEvent =>
  ({ run_id: "r", ...over } as RunEvent);

const plan = {
  steps: [
    { id: "s1", agent: "jira-analyst", task: "fetch bugs",
      tools: ["jira_get_sprints", "jira_get_sprint_issues"], depends_on: [] },
    { id: "s2", agent: "data-scientist", task: "trend",
      tools: ["run_python"], depends_on: [] },
  ],
  synthesis: "combine",
};

describe("buildWorkflowModel", () => {
  it("builds a vertical full graph with tools as nodes", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "Compare bug counts" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.PlanCreated, seq: 2, ts: "2026-09-05T12:00:02Z",
        plan: { steps: plan.steps, synthesis: "combine", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.NodeStarted, seq: 3, ts: "2026-09-05T12:00:03Z", node: "s1" }),
      ev({ type: EventType.NodeStarted, seq: 4, ts: "2026-09-05T12:00:03Z", node: "s2" }),
      ev({ type: EventType.ToolCallStarted, seq: 5, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_get_sprints", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 6, ts: "2026-09-05T12:00:03.040Z",
        step_id: "s1", tool: "jira_get_sprints", status: "ok", ms: 40,
        digest_preview: "5 sprints", result_id: "tr-1" }),
      ev({ type: EventType.ToolCallStarted, seq: 7, ts: "2026-09-05T12:00:03.050Z",
        step_id: "s1", tool: "jira_get_sprint_issues", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 8, ts: "2026-09-05T12:00:03.080Z",
        step_id: "s1", tool: "jira_get_sprint_issues", status: "ok", ms: 25,
        digest_preview: "12 issues", result_id: "tr-2" }),
      ev({ type: EventType.ToolCallStarted, seq: 9, ts: "2026-09-05T12:00:03.010Z",
        step_id: "s2", tool: "run_python", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 10, ts: "2026-09-05T12:00:04.200Z",
        step_id: "s2", tool: "run_python", status: "ok", ms: 200,
        digest_preview: "steady", result_id: "tr-3" }),
      ev({ type: EventType.NodeFinished, seq: 11, ts: "2026-09-05T12:00:04Z",
        node: "s1", summary: "Retrieved 2 tool result(s)" }),
      ev({ type: EventType.NodeFinished, seq: 12, ts: "2026-09-05T12:00:05Z",
        node: "s2", summary: "Bug volume holding steady" }),
      ev({ type: EventType.NodeStarted, seq: 13, ts: "2026-09-05T12:00:05Z", node: "synthesis" }),
      ev({ type: EventType.NodeFinished, seq: 14, ts: "2026-09-05T12:00:08Z",
        node: "synthesis", summary: "Bug volume has been holding steady" }),
      ev({ type: EventType.RunFinished, seq: 15, ts: "2026-09-05T12:00:08.200Z",
        result: "done", totals: {} }),
    ];
    const model = buildWorkflowModel(plan, events, { goal: "Compare bug counts" } as never);
    const kinds = model.nodes.map((n) => n.kind);
    expect(kinds).toContain("goal");
    expect(kinds).toContain("router");
    expect(kinds).toContain("plan");
    expect(kinds).toContain("agent");
    expect(kinds).toContain("tool");
    expect(kinds).toContain("answer");
    expect(model.toolCount).toBe(3);
    expect(model.parallelAgents).toBe(true);
    expect(model.edges.some((e) => e.source === "s1" && e.target === "s2")).toBe(false);
    expect(model.edges.some((e) => e.source === "s2" && e.target.startsWith("tool-s2-"))).toBe(true);
    expect(model.edges.some((e) => e.source.startsWith("tool-s1-") && e.target.startsWith("tool-s2-"))).toBe(false);

    const pos = workflowPositions(model.nodes);
    expect(pos.goal.y).toBeLessThan(pos.router.y);
    expect(pos.router.y).toBeLessThan(pos.planner.y);
    expect(pos.planner.y).toBeLessThan(pos.s1.y);
    expect(pos.s1.y).toBe(pos.s2.y);
    expect(pos.s1.x).not.toBe(pos.s2.x);
    expect(pos.synthesis.y).toBeGreaterThan(pos.s1.y);
  });

  it("stacks a dependent agent under the previous agent's last tool", () => {
    const sequential = {
      steps: [
        { id: "s1", agent: "jira-analyst", task: "fetch",
          tools: ["jira_get_sprints", "jira_get_sprint_issues"], depends_on: [] },
        { id: "s2", agent: "data-scientist", task: "trend",
          tools: ["run_python"], depends_on: ["s1"] },
      ],
      synthesis: "combine",
    };
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "bugs" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.PlanCreated, seq: 2, ts: "2026-09-05T12:00:02Z",
        plan: { steps: sequential.steps, synthesis: "combine", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.NodeStarted, seq: 3, ts: "2026-09-05T12:00:03Z", node: "s1" }),
      ev({ type: EventType.ToolCallStarted, seq: 4, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_get_sprints", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 5, ts: "2026-09-05T12:00:03.040Z",
        step_id: "s1", tool: "jira_get_sprints", status: "ok", ms: 40,
        digest_preview: "sprints", result_id: "a" }),
      ev({ type: EventType.ToolCallStarted, seq: 6, ts: "2026-09-05T12:00:03.050Z",
        step_id: "s1", tool: "jira_get_sprint_issues", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 7, ts: "2026-09-05T12:00:03.080Z",
        step_id: "s1", tool: "jira_get_sprint_issues", status: "ok", ms: 25,
        digest_preview: "PAY-SPR-1", result_id: "b" }),
      ev({ type: EventType.ToolCallStarted, seq: 8, ts: "2026-09-05T12:00:03.090Z",
        step_id: "s1", tool: "jira_get_sprint_issues", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 9, ts: "2026-09-05T12:00:03.120Z",
        step_id: "s1", tool: "jira_get_sprint_issues", status: "ok", ms: 25,
        digest_preview: "NOVA-3", result_id: "c" }),
      ev({ type: EventType.NodeFinished, seq: 10, ts: "2026-09-05T12:00:04Z",
        node: "s1", summary: "Retrieved 3 tool result(s)" }),
      ev({ type: EventType.NodeStarted, seq: 11, ts: "2026-09-05T12:00:04Z", node: "s2" }),
      ev({ type: EventType.ToolCallStarted, seq: 12, ts: "2026-09-05T12:00:04Z",
        step_id: "s2", tool: "run_python", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 13, ts: "2026-09-05T12:00:04.200Z",
        step_id: "s2", tool: "run_python", status: "ok", ms: 200,
        digest_preview: "steady", result_id: "d" }),
      ev({ type: EventType.NodeFinished, seq: 14, ts: "2026-09-05T12:00:05Z",
        node: "s2", summary: "holding steady" }),
      ev({ type: EventType.NodeStarted, seq: 15, ts: "2026-09-05T12:00:05Z", node: "synthesis" }),
      ev({ type: EventType.NodeFinished, seq: 16, ts: "2026-09-05T12:00:08Z",
        node: "synthesis", summary: "done" }),
      ev({ type: EventType.RunFinished, seq: 17, ts: "2026-09-05T12:00:08.200Z",
        result: "done", totals: {} }),
    ];
    const model = buildWorkflowModel(sequential, events, { goal: "bugs" } as never);
    expect(model.parallelAgents).toBe(false);
    expect(model.edges.some((e) => e.source === "s1" && e.target === "s2")).toBe(false);
    expect(model.edges.some((e) => e.source.startsWith("tool-s1-") && e.target === "s2")).toBe(true);
    expect(model.edges.some((e) => e.source === "s2" && e.target.startsWith("tool-s2-"))).toBe(true);
    expect(model.edges.some((e) => e.source.startsWith("tool-s2-") && e.target === "synthesis")).toBe(true);
    expect(model.edges.some((e) => e.source.startsWith("tool-s1-") && e.target === "synthesis")).toBe(false);

    const pos = workflowPositions(model.nodes);
    const s1Tools = model.nodes.filter((n) => n.id.startsWith("tool-s1-"));
    const lastS1 = s1Tools.reduce((a, b) => (a.layer > b.layer ? a : b));
    expect(pos.s1.y).toBeLessThan(pos[lastS1.id].y);
    expect(pos[lastS1.id].y).toBeLessThan(pos.s2.y);
    expect(pos.s2.y).toBeLessThan(pos["tool-s2-13"].y);
    expect(pos["tool-s2-13"].y).toBeLessThan(pos.synthesis.y);
    expect(pos.s1.x).toBe(pos.s2.x);
    expect(pos.s1.x).toBe(pos.synthesis.x);
  });

  it("marks overlapping tools on the same agent as parallel", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "x" }),
      ev({ type: EventType.ToolCallStarted, seq: 1, ts: "2026-09-05T12:00:01.000Z",
        step_id: "s1", tool: "jira_get_sprints", args_digest: {} }),
      ev({ type: EventType.ToolCallStarted, seq: 2, ts: "2026-09-05T12:00:01.005Z",
        step_id: "s1", tool: "jira_get_board", args_digest: {} }),
      ev({ type: EventType.ToolCallFinished, seq: 3, ts: "2026-09-05T12:00:01.080Z",
        step_id: "s1", tool: "jira_get_sprints", status: "ok", ms: 80,
        digest_preview: "ok", result_id: "a" }),
      ev({ type: EventType.ToolCallFinished, seq: 4, ts: "2026-09-05T12:00:01.090Z",
        step_id: "s1", tool: "jira_get_board", status: "ok", ms: 80,
        digest_preview: "ok", result_id: "b" }),
      ev({ type: EventType.RunFinished, seq: 5, ts: "2026-09-05T12:00:02Z", result: "ok", totals: {} }),
    ];
    const model = buildWorkflowModel({
      steps: [{ id: "s1", agent: "jira-analyst", task: "fetch", tools: [], depends_on: [] }],
      synthesis: "sum",
    }, events, null);
    const toolNodes = model.nodes.filter((n) => n.kind === "tool");
    expect(toolNodes).toHaveLength(2);
    expect(toolNodes.every((n) => n.concurrency === "parallel")).toBe(true);
    expect(model.parallelTools).toBe(true);
    expect(model.edges.some((e) => e.kind === "parallel")).toBe(true);
  });

  it("attaches popup details and tool result ids", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "Compare bug counts" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.PlanCreated, seq: 2, ts: "2026-09-05T12:00:02Z",
        plan: { steps: plan.steps, synthesis: "combine", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.ToolCallStarted, seq: 3, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_get_sprints", args_digest: { board: "PAY" } }),
      ev({ type: EventType.ToolCallFinished, seq: 4, ts: "2026-09-05T12:00:03.040Z",
        step_id: "s1", tool: "jira_get_sprints", status: "ok", ms: 40,
        digest_preview: "5 sprints", result_id: "tr-1" }),
      ev({ type: EventType.RunFinished, seq: 5, ts: "2026-09-05T12:00:04Z", result: "ok", totals: {} }),
    ];
    const model = buildWorkflowModel(plan, events, { goal: "Compare bug counts" } as never);
    const goal = model.nodes.find((n) => n.id === "goal");
    expect(goal?.body).toContain("Compare bug counts");
    const tool = model.nodes.find((n) => n.resultId === "tr-1");
    expect(tool?.fields?.some((f) => f.label === "Duration" && f.value === "40ms")).toBe(true);
    expect(tool?.fields?.some((f) => f.label === "board" && f.value === "PAY")).toBe(true);
    expect(tool?.body).toContain("5 sprints");
  });

  it("attaches input/output on inspectable agent and router nodes", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "bugs" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "complex", path: "plan", reason: "work", temporal_sensitivity: true }),
      ev({ type: EventType.PlanCreated, seq: 2, ts: "2026-09-05T12:00:02Z",
        plan: { steps: plan.steps, synthesis: "combine", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.AgentSpawned, seq: 3, ts: "2026-09-05T12:00:02.100Z",
        step_id: "s1", agent: "jira-analyst", tools: ["jira_get_sprints"] }),
      ev({ type: EventType.NodeStarted, seq: 4, ts: "2026-09-05T12:00:03Z", node: "s1" }),
      ev({ type: EventType.ToolCallStarted, seq: 5, ts: "2026-09-05T12:00:03Z",
        step_id: "s1", tool: "jira_get_sprints", args_digest: { board: "PAY" } }),
      ev({ type: EventType.ToolCallFinished, seq: 6, ts: "2026-09-05T12:00:03.040Z",
        step_id: "s1", tool: "jira_get_sprints", status: "ok", ms: 40,
        digest_preview: "5 sprints", result_id: "tr-1" }),
      ev({ type: EventType.NodeFinished, seq: 7, ts: "2026-09-05T12:00:04Z",
        node: "s1", summary: "Retrieved 1 tool result(s)" }),
      ev({ type: EventType.RunFinished, seq: 8, ts: "2026-09-05T12:00:05Z", result: "ok", totals: {} }),
    ];
    const model = buildWorkflowModel(plan, events, { goal: "bugs" } as never);
    const agent = model.nodes.find((n) => n.id === "s1");
    expect(agent?.inspectable).toBe(true);
    expect(agent?.input).toContain("fetch bugs");
    expect(agent?.input).toContain("jira_get_sprints");
    expect(agent?.output).toContain("Retrieved 1 tool result(s)");
    expect(agent?.output).toContain("jira_get_sprints");
    const tool = model.nodes.find((n) => n.resultId === "tr-1");
    expect(tool?.input).toContain("PAY");
    expect(tool?.output).toContain("5 sprints");
    const router = model.nodes.find((n) => n.id === "router");
    expect(router?.inspectable).toBe(true);
    expect(router?.input).toContain("bugs");
    expect(router?.output).toContain("plan");
  });

  it("shows tools offered to LLM from context snapshot instead of full allowlist", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "projects" }),
      ev({ type: EventType.PlanCreated, seq: 1, ts: "2026-09-05T12:00:01Z",
        plan: { steps: plan.steps, synthesis: "list", uses_episodic_hint: false }, hint_used: false }),
      ev({ type: EventType.AgentSpawned, seq: 2, ts: "2026-09-05T12:00:01.100Z",
        step_id: "s1", agent: "jira-analyst",
        tools: ["jira_get_projects", "jira_get_sprints", "jira_search"] }),
      ev({ type: EventType.ContextSnapshot, seq: 3, ts: "2026-09-05T12:00:01.200Z",
        node: "s1",
        sections: [{
          key: "bindings",
          title: "Prompt bindings",
          format: "json",
          content: JSON.stringify({
            tool_execution: "mcp",
            llm_tool_binding: "openai_tools_api",
            tools_registered: 2,
            tools_allowlist_count: 27,
          }),
        }] }),
      ev({ type: EventType.RunFinished, seq: 4, ts: "2026-09-05T12:00:02Z", result: "ok", totals: {} }),
    ];
    const model = buildWorkflowModel(plan, events, { goal: "projects" } as never);
    const agent = model.nodes.find((n) => n.id === "s1");
    expect(agent?.input).toContain("Tool execution: mcp");
    expect(agent?.input).toContain("LLM tool binding: openai_tools_api (2 schemas registered)");
    expect(agent?.input).toContain("Agent allowlist (YAML): 27 tools bound at spawn");
    expect(agent?.input).not.toContain("jira_search");
  });

  it("attaches context snapshot sections to workflow nodes", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "bugs" }),
      ev({ type: EventType.ContextSnapshot, seq: 1, ts: "2026-09-05T12:00:00.100Z",
        node: "goal",
        sections: [
          { key: "conversation", title: "Session memory", content: "User: NOVA-4", format: "text" },
          { key: "entities", title: "Resolved entities", content: '{"issue_id":"NOVA-4"}', format: "json" },
        ] }),
      ev({ type: EventType.RouterDecision, seq: 2, ts: "2026-09-05T12:00:01Z",
        intent: "simple", path: "context_recall", reason: "session", temporal_sensitivity: false }),
      ev({ type: EventType.ContextSnapshot, seq: 3, ts: "2026-09-05T12:00:01.100Z",
        node: "router",
        sections: [
          { key: "conversation", title: "Session memory", content: "User: NOVA-4", format: "text" },
          { key: "note", title: "Routing note", content: "Answered from session memory", format: "text" },
        ] }),
      ev({ type: EventType.RunFinished, seq: 4, ts: "2026-09-05T12:00:02Z", result: "Leo", totals: {} }),
    ];
    const model = buildWorkflowModel(null, events, { goal: "bugs", result: "Leo" } as never);
    const goal = model.nodes.find((n) => n.id === "goal");
    expect(goal?.contextSections).toHaveLength(2);
    expect(goal?.contextSections?.[0].key).toBe("conversation");
    const router = model.nodes.find((n) => n.id === "router");
    expect(router?.contextSections?.some((s) => s.key === "note")).toBe(true);
  });

  it("renders a fast-path graph without a planner plan", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z", goal: "hello" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:00.020Z",
        intent: "simple", path: "fast", reason: "chat", temporal_sensitivity: false }),
      ev({ type: EventType.RunFinished, seq: 2, ts: "2026-09-05T12:00:00.400Z",
        result: "Hi there", totals: {} }),
    ];
    const model = buildWorkflowModel(null, events, { goal: "hello", result: "Hi there" } as never);
    expect(model.nodes.map((n) => n.id)).toEqual(["goal", "router", "answer"]);
    const pos = workflowPositions(model.nodes);
    expect(pos.goal.y).toBeLessThan(pos.router.y);
    expect(pos.router.y).toBeLessThan(pos.answer.y);
    expect(pos.goal.x).toBe(pos.router.x);
  });

  it("shows clarify HITL on the spine between router and planner", () => {
    const events: RunEvent[] = [
      ev({ type: EventType.RunStarted, seq: 0, ts: "2026-09-05T12:00:00Z",
        goal: "on what task he is currently working on?\n\nUser clarification: Alex" }),
      ev({ type: EventType.RouterDecision, seq: 1, ts: "2026-09-05T12:00:01Z",
        intent: "clarify", path: "fast", reason: "router LLM: clarify", temporal_sensitivity: true }),
      ev({ type: EventType.ClarifyRequired, seq: 2, ts: "2026-09-05T12:00:01.050Z",
        question: "Which person are you asking about?", reason: "router" }),
      ev({ type: EventType.PlanCreated, seq: 3, ts: "2026-09-05T12:00:02Z",
        plan: { steps: plan.steps.slice(0, 1), synthesis: "combine", uses_episodic_hint: false },
        hint_used: false }),
      ev({ type: EventType.NodeStarted, seq: 4, ts: "2026-09-05T12:00:03Z", node: "s1" }),
      ev({ type: EventType.NodeFinished, seq: 5, ts: "2026-09-05T12:00:04Z",
        node: "s1", summary: "Checked Alex workload" }),
      ev({ type: EventType.NodeStarted, seq: 6, ts: "2026-09-05T12:00:04Z", node: "synthesis" }),
      ev({ type: EventType.NodeFinished, seq: 7, ts: "2026-09-05T12:00:05Z",
        node: "synthesis", summary: "Alex has no matching tasks" }),
      ev({ type: EventType.RunFinished, seq: 8, ts: "2026-09-05T12:00:05.200Z",
        result: "Alex has no matching tasks", totals: {} }),
    ];
    const model = buildWorkflowModel(
      { steps: plan.steps.slice(0, 1), synthesis: "combine" },
      events,
      {
        goal: "on what task he is currently working on?\n\nUser clarification: Alex",
        result: "Alex has no matching tasks",
        status: "completed",
      } as never,
    );
    const clarify = model.nodes.find((node) => node.kind === "clarify");
    expect(clarify).toBeTruthy();
    expect(clarify?.output).toContain("Alex");
    expect(model.path).toContain("clarify");
    expect(model.edges.some((edge) => edge.source === "router" && edge.target === clarify?.id)).toBe(true);
    expect(model.edges.some((edge) => edge.source === clarify?.id && edge.target === "planner")).toBe(true);
    const pos = workflowPositions(model.nodes);
    expect(pos.router.y).toBeLessThan(pos[clarify!.id].y);
    expect(pos[clarify!.id].y).toBeLessThan(pos.planner.y);
  });
});
