# Demo script (~5 minutes)

Use **Technical** view in the UI to show the live trace and workflow graph.

## Prerequisites

```powershell
.\scripts\start_all.ps1
# UI: http://localhost:5173  |  API docs: http://localhost:8000/docs
```

Ensure `LLM_PROVIDER=openai` (or `auto` with a key) in `backend/.env` for live reasoning. MockLLM works for structure-only demos.

## 1. Fast path (~30s)

**Prompt:** `Hi, what can you do?`

**Show:** `router_decision` with `path=fast` — no planner, no graph spawn. Conversational agent answers directly.

## 2. Multi-agent plan path (~2 min)

**Prompt:** `Predict the velocity for our next sprint based on previous Jira sprints`

**Show:**

- `plan_created` with DAG steps (`jira-analyst` → `data-scientist` → synthesis)
- `agent_spawned` with bound tool lists from YAML
- `tool_call_started/finished` on Jira MCP (streamable-http) then sandbox `run_python` (stdio)
- Final synthesized answer citing sprint metrics

## 3. Sequential analysis (~1 min)

**Prompt:** `Compare bug counts across the last 3 sprints and analyze their trend`

**Show:** Planner may parallelize independent fetches or sequence fetch → analyze depending on plan; worker ReAct rounds in trace.

## 4. Human-in-the-loop (~1 min)

**Prompt:** `Create a Jira issue that tracks the velocity forecast for the next sprint`

**Show:** Run parks at `awaiting_approval` → Approve/Reject in UI → resume or skip write tool.

## 5. Session memory (~30s)

Repeat prompt #2 in the **same session**.

**Show:** Planner receives episodic hint (`plan_created.hint_used`); fresh tool data still fetched (plans-only memory, not cached answers).

## Wrap-up

- Open **Run history** and replay traces via `GET /api/runs/{id}/traces`
- Mention keyless pytest suite: `cd backend && pytest tests -q`
