import { describe, expect, it } from "vitest";

import { EventType, type RunEvent } from "./events";
import { deriveRunProgress } from "./runProgress";

describe("deriveRunProgress", () => {
  it("shows planner label while llm call is in flight", () => {
    const events = [
      { type: EventType.LlmCallStarted, node: "planner", label: "Building a plan…", seq: 1, run_id: "r", ts: "" },
    ] as RunEvent[];
    const progress = deriveRunProgress(events);
    expect(progress.title).toBe("Planner");
    expect(progress.detail).toContain("Building a plan");
    expect(progress.phase).toBe("plan");
  });

  it("shows tool fetch detail when a tool call is in flight", () => {
    const events = [
      { type: EventType.ToolCallStarted, tool: "jira_get_sprints", step_id: "s1", seq: 2, run_id: "r", ts: "" },
    ] as RunEvent[];
    const progress = deriveRunProgress(events);
    expect(progress.detail).toContain("Jira get sprints");
    expect(progress.detail).toContain("Fetching data");
  });

  it("shows clarify prompt while waiting for user input", () => {
    const events = [
      { type: EventType.ClarifyRequired, question: "Which project?", seq: 1, run_id: "r", ts: "" },
    ] as RunEvent[];
    const progress = deriveRunProgress(events);
    expect(progress.title).toBe("Needs your input");
    expect(progress.detail).toBe("Which project?");
    expect(progress.phase).toBe("clarify");
  });
});
