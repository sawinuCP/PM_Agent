# Architecture — Dynamic AI Agent Orchestration Platform

> System design reference: diagram, agent routing, HITL, frameworks, MCP integration, memory, and model constraints.

## 1. System diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         React frontend (Vite + TypeScript)                   │
│  Simple chat · Technical trace · Workflow graph · HITL clarify/approval UI     │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │  REST  +  SSE (/api/runs/{id}/events)
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI orchestrator API                               │
│  sessions · chat · runs · traces · approve · health · agents catalog           │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
 ┌─────────────┐        ┌─────────────┐        ┌─────────────────┐
 │ ChatService │        │  EventBus   │        │ Platform bootstrap│
 │ resume HITL │───────►│ SSE + traces│        │ registry · MCP ·  │
 │ clarify run │        │ + token use │        │ LLM provider      │
 └──────┬──────┘        └─────────────┘        └─────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         run_turn (pipeline)                                    │
│                                                                                │
│   ┌──────────┐    ┌──────────┐    ┌─────────────────────────────────────┐   │
│   │  ROUTER  │───►│ PLANNER  │───►│ EXECUTOR (LangGraph per request)     │   │
│   │ cheap LLM│    │ strong   │    │ spawn agents · DAG fan-out/in · synth│   │
│   └────┬─────┘    └────┬─────┘    └──────────────────┬──────────────────┘   │
│        │ fast/clarify   │ plan or clarify              │ workers + HITL      │
└────────┼────────────────┼──────────────────────────────┼──────────────────────┘
         │                │                              │
         ▼                ▼                              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    MCP Manager (single boundary)                               │
│  discovery · RBAC · retry/CB · digest/preview · tool_results persistence       │
└───────────────┬──────────────────────────────┬───────────────────────────────┘
                ▼                              ▼
     ┌────────────────────┐         ┌────────────────────┐
     │ Jira MCP           │         │ Code-Sandbox MCP   │
     │ streamable-http    │         │ stdio run_python   │
     │ in-memory simulator│         │ guarded subprocess │
     └────────────────────┘         └────────────────────┘
                │
                ▼
     SQLite (keyless default) or PostgreSQL 16 + pgvector
     sessions · runs · traces · tool_results · messages · checkpoints · memory
```

### 1.1 Request lifecycle (plan path)

```
User message
    │
    ▼
Router ──fast──► conversational answer (no graph)
    │
    └──clarify──► park run ──► UI: clarify_required ──► user reply ──► resume (same run_id)
    │
    └──plan──► Planner ──clarify──► park (as above)
              │
              └──Plan (DAG) ──► LangGraph execute ──► synthesis ──► answer
                                    │
                                    └──approval_required──► UI Approve/Reject
```

Clarify resume (`chat_service` → `Executor.resume_with_clarification`):

1. Append user answer to session messages.
2. Enrich goal: `{original}\n\nUser clarification: {answer}`.
3. Re-run planner; if planner asks clarify again after user already answered, **coerce** a best-effort `jira-analyst` step (`coerce_plan_after_clarify`).
4. Execute plan or park again (max `MAX_CLARIFY_REPLANS`).

---

## 2. Agent architecture & routing

**Three-tier decision cascade** (cheapest sufficient path per message):

| Stage | Component | Output |
|---|---|---|
| 1 | Heuristic pre-filter | Greetings → fast path; temporal keywords flagged |
| 2 | Cheap LLM router | `{intent, direct_answer, temporal_sensitivity}` |
| 3 | Planner (strong tier) | Schema-validated `Plan` (DAG) or `clarify_question` |

**Planner output:** WHO and WHAT, not HOW. Tool names in planner JSON are stripped by `sanitize_plan()` — workers bind the YAML allowlist at spawn and choose tools from MCP descriptions in a ReAct loop.

**Registered agents** (`config/agents/`):

| Agent | Role |
|---|---|
| `conversational` | Fast-path chat, meta questions |
| `jira-analyst` | Jira read/fetch specialist (large tool allowlist) |
| `data-scientist` | Numerical analysis via sandbox `run_python` |
| `jira-writer` | Jira writes (approval-gated) |

**Worker execution** (`orchestration/graph/worker.py`):

- Directives: planner `assigned_task` + YAML `system_prompt`.
- ReAct: native OpenAI tool-calling; parallel reads (`asyncio.gather`, up to `worker_max_tools_per_wave`).
- Guards: dedup, harvest hints, entity coverage gates, tool-error recovery, round caps.
- `jira-analyst` receives full bound allowlist (≤ `worker_tool_shortlist_max` = 32).

**Parallelism:**

- **DAG level:** independent steps share `START` edges; `plan_deps.py` normalizes join dependencies.
- **Tool level:** multiple read tools in one LLM turn when the model emits parallel calls.

---

## 3. Human-in-the-loop (HITL)

| Kind | Trigger | Run status | UI | Resume |
|---|---|---|---|---|
| **Clarify** | Router or planner needs scope | `awaiting_clarification` | Question in chat + “Your answer” composer | Next `/api/chat` message on same session |
| **Approval** | Sensitive MCP write | `awaiting_approval` | Approve/Reject card | `POST /api/runs/{id}/approve` |

Backend: `orchestration/workflow/clarify.py`, `orchestration/hitl/approval.py`.  
Frontend: `pendingClarify` / `pendingApproval` trace state, clarify resume SSE generation bump, no synthesis spinner during clarify wait.

---

## 4. LLM provider & model tiers

All live calls go through an **OpenAI-compatible client** (`app/ai/provider.py`).

| Setting | Default (this repo) |
|---|---|
| Provider | CommandCode (`OPENAI_BASE_URL`) |
| Model | `z-ai/glm-5.3-flash` |
| Router / fast | tier `cheap`, reasoning `low` |
| Workers | tier `standard`, reasoning `high` |
| Planner | tier `planner`, reasoning `high` |

**Pytest** forces `mock` — deterministic scripted router/planner/worker behavior.

### 4.1 Model capability note (GLM-5.3-Flash)

This is a **flash-tier general model**, not a deep reasoning agent. The platform compensates with:

- Structured planner output + plan validation
- YAML role prompts per agent
- Code-side coverage gates and harvest hints
- HITL clarify and write approval
- Compact hop previews to stay within useful context

It does **not** guarantee optimal tool chains, minimal clarify, or strong multi-hop reasoning. Evaluators should judge **orchestration architecture and observability** separately from raw model IQ. Swap `OPENAI_MODEL` for a stronger endpoint to reduce tool-selection errors.

---

## 5. Framework usage

| Framework | Role |
|---|---|
| **LangGraph ≥1.2** | Fresh `StateGraph` per request, DAG edges, checkpointer, HITL gate |
| **LangChain ≥1.4** | Provider bindings, structured output, tool bridge (not `langchain.mcp`) |
| **FastAPI** | REST + SSE + OpenAPI |
| **MCP SDK v2** | Streamable HTTP (Jira) + stdio (sandbox) |

---

## 6. MCP integration

- **Single boundary:** `app/mcp_integration/manager.py` only.
- **Discovery:** TTL-cached `tools/list`; planner sees live catalogs.
- **Resilience:** timeout, backoff, circuit breaker; semantic retries in `tool_retry.py`.
- **Hot path:** trace events carry **digest/preview**; full payloads in `tool_results` (+ UI drill-down).
- **Jira:** in-memory simulator with deterministic seed data (`PLAT`, `NOVA`, …).

---

## 7. Memory design

| Layer | Scope | Mechanism |
|---|---|---|
| L1 working | per agent/run | Token budget trim before each LLM call |
| L2 conversation | per thread | 70/30 summarization hysteresis |
| L3 episodic | cross-session | **Plans only**, recency-weighted, 14-day TTL |
| L4 semantic | user/global | Post-run extraction → pgvector (or hash fallback) |

**L1 policy:** role, task, and upstream block (≤3200 chars) are protected; old semantic/episodic hints shrink first.

**Staleness defense:** plans-not-answers → recency + TTL → router temporal flag → synthesis citation rules.

---

## 8. Execution evidence path

Workers do not receive full MCP JSON on every hop:

1. Manager returns `preview` + `digest`; persists full payload asynchronously.
2. `hop_evidence.py`: 8-hop sliding window, ~8k char serialized cap per hop.
3. Synthesis aggregates step evidence with a higher cap in `digest.py`.

---

## 9. Security & deployment

- Keyless localhost demo; optional `API_TOKEN` for mutations.
- `CORS_ORIGINS` explicit; compose binds Postgres/MCP to loopback.
- Sandbox: disposable child process + import guardrails — not a microVM.
- Secrets only in `backend/.env` (gitignored); `.env.example` has empty keys.

---

## 10. Testing & observability

| Suite | Command | Notes |
|---|---|---|
| Backend | `pytest tests -q` | 227 tests, MockLLM + fake MCP |
| Frontend | `npm test` | 58 tests, SSE state + UI helpers |
| E2E smoke | `python scripts/e2e_smoke.py` | In-process real MCP transports |

Every run emits a persisted, replayable trace suitable for demo review and regression comparison.
