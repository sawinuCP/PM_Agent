# Dynamic AI Agent Orchestration Platform

A **dynamic, configuration-driven multi-agent orchestration platform** (Python + MCP) with a React trace viewer. Submit a natural-language goal; the platform classifies it, plans a DAG of specialized agents, spawns them at runtime, provisions each with task-appropriate MCP tools, coordinates execution (fan-out/fan-in), and streams a live execution trace to the UI.

> **Monolithic single-agent prompts are explicitly out of scope** — there is no hard-coded agent with a giant prompt. Agents are YAML templates; the plan and graph are built per request.

**LLM switch:** set `LLM_PROVIDER` in `backend/.env` only (`mock` | `openai` | `auto`). Pytest pins `mock` so the suite stays keyless and deterministic.

**Live model (current test config):** `meta/muse-spark-1.3-contributor` via [CommandCode](https://api.commandcode.ai) (`OPENAI_BASE_URL` + `OPENAI_API_KEY` in `backend/.env`). Muse Spark is a reasoning model — set `LLM_REASONING_EFFORT_*` tiers (`medium` for workers/planner in `.env`). Swap back to `z-ai/glm-5.3-flash` anytime for the cheaper flash baseline.

---

## Quick start

### Option A — Docker infra + local API/UI (recommended)

```powershell
# First-time setup:
#   python -m venv .venv
#   .\.venv\Scripts\pip install -e "backend[dev]"
#   cd frontend; npm install
#   copy backend\.env.example backend\.env   # fill OPENAI_API_KEY, VOYAGE_API_KEY

.\scripts\start_local.ps1
# Docker: Postgres :5433, Jira MCP :8001
# Host:   backend :8000, frontend :5173 (Vite proxies /api -> backend)
```

`backend/.env` should point at the Docker services:

```env
DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5433/orchestrator
CHECKPOINT_DB_PATH=
JIRA_MCP_HOST=127.0.0.1
JIRA_MCP_PORT=8001
```

### Option B — full Docker stack

```bash
cp backend/.env.example backend/.env
docker compose --profile full up --build
```

Uses the `full` profile for backend + frontend containers. Default `docker compose up -d postgres jira-mcp` is enough when running API/UI on the host (Option A).

### Option C — manual terminals

```powershell
# Terminal 1 — Docker infra (Postgres + Jira MCP)
docker compose up -d postgres jira-mcp

# Terminal 2 — backend API (reads backend/.env)
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 3 — frontend (Vite proxies /api -> :8000)
cd frontend
npm run dev   # http://127.0.0.1:5173
```

### Run the tests

```powershell
cd backend; ..\.venv\Scripts\python.exe -m pytest tests -q   # 227 tests, keyless
cd frontend; npm test                                        # 58 tests
```

The backend suite uses MockLLM + in-process fake MCP + deterministic hash embeddings — no network, no keys.

**Live validation** (requires `LLM_PROVIDER=openai`, keys in `backend/.env`, and running services):

```powershell
.\.venv\Scripts\python.exe backend\scripts\e2e_smoke.py
.\.venv\Scripts\python.exe backend\scripts\live_dataset_runner.py
.\.venv\Scripts\python.exe backend\scripts\live_answer_verify.py
```

Results from live runs are written under `backend/data/` (gitignored).

The UI opens in a chat-first **Simple** view. A persisted **Technical** toggle reveals the live trace, workflow graph, token usage, costs, run history, and tool-result drill-down. **HITL** (clarify + approval) is visible in both views.

---

## Example prompts

| # | Prompt | What it demonstrates |
|---|---|---|
| 1 | `Hi, what can you do?` | Fast path: router → conversational agent, no graph spawn |
| 2 | `Predict the velocity for our next sprint based on previous Jira sprints` | Planner → `jira-analyst` (Jira MCP) → `data-scientist` (sandbox) → synthesis |
| 3 | `Compare bug counts across the last 3 sprints and analyze their trend` | Multi-hop fetch → analysis |
| 4 | `What are the open Jira tickets in this sprint?` | **HITL clarify** when scope is ambiguous (reply with project/sprint, e.g. `NOVA`) |
| 5 | `Create a Jira issue that tracks the velocity forecast for the next sprint` | **HITL approval** on sensitive write → UI Approve/Reject |
| 6 | Ask #2 again in the same session | Episodic memory: planner hint from prior plan, fresh data via live tools |

For a timed walkthrough, see [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

Every run streams typed SSE events (`run_started`, `router_decision`, `plan_created`, `clarify_required`, `agent_spawned`, `tool_call_started/finished`, `approval_required`, `token_usage`, `node_finished`, `run_finished`) and persists them for replay (`GET /api/runs/{id}/traces`).

---

## Project structure

```
Azend/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory + lifespan
│   │   ├── api/                    # sessions · chat · runs(+SSE) · system
│   │   ├── orchestration/          # routing · planning · graph · workflow · coverage · tooling · hitl
│   │   ├── agents/                 # registry (YAML) · factory · worker (L1 budgets)
│   │   ├── mcp_integration/        # manager (sole MCP boundary) · transports · digest
│   │   ├── memory/                 # checkpointer · conversation · episodic · semantic
│   │   ├── ai/                     # provider · prompts · cost · reasoning
│   │   ├── observability/          # typed events · event bus → SSE + traces
│   │   ├── models/                 # SQLAlchemy · Pydantic contracts
│   │   ├── services/               # chat_service · platform bootstrap
│   │   └── core/                   # settings · logging · errors
│   ├── mcp_servers/                # jira/ (http MCP) · sandbox/ (run_python)
│   ├── config/agents/              # conversational · jira-analyst · data-scientist · jira-writer
│   ├── tests/                      # unit · integration · fakes.py
│   └── scripts/                    # e2e_smoke · live_dataset_runner · ops probes
├── frontend/src/                   # chat UI · trace · workflow graph · HITL composer
├── docs/
│   ├── ARCHITECTURE.md             # system design + diagrams
│   ├── DEMO_SCRIPT.md
│   ├── TEST_DATASET.md
│   └── REQUIREMENTS_TRACEABILITY.md
├── docker-compose.yml              # postgres + jira-mcp (backend/frontend optional)
├── scripts/start_all.ps1
└── README.md
```

---

## Environment variables

See `backend/.env.example` — copy to `backend/.env`; **no real secrets in the repo**.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `mock` (pytest) · `openai` (live) · `auto` (live if keyed) |
| `OPENAI_API_KEY` | — | CommandCode or any OpenAI-compatible key |
| `OPENAI_BASE_URL` | `https://api.commandcode.ai/provider/v1` | Provider endpoint |
| `OPENAI_MODEL` / `LLM_PLANNER_MODEL` | `meta/muse-spark-1.3-contributor` | Worker/router vs planner model |
| `LLM_REASONING_EFFORT_STANDARD` | `medium` | Agent/worker reasoning depth (Muse Spark) |
| `LLM_REASONING_EFFORT_*` | cheap=`low`, standard/planner=`high` | GLM reasoning depth per tier |
| `WORKER_MAX_TOOL_ROUNDS` / `_ANALYST` / `_TOOLS_PER_WAVE` | `4` / `3` / `6` | ReAct caps and parallel read batch |
| `VOYAGE_API_KEY` / `VOYAGE_MODEL` | — / `voyage-3.5-lite` | Semantic embeddings (1024 dims) |
| `DATABASE_URL` | SQLite file | Postgres: `postgresql+asyncpg://user:pass@host:5432/db` |
| `MCP_SERVERS_CONFIG` | `config/mcp_servers.yaml` | MCP fleet config |
| `API_TOKEN` | empty | Optional `X-API-Key` for mutation routes |

Optional Postgres + Voyage: point `DATABASE_URL` at Postgres, set `CHECKPOINT_DB_PATH=` empty to share the checkpointer, add `VOYAGE_API_KEY`, run `python scripts/migrate_voyage_1024.py` after dimension changes. Verify with `GET /api/health/ready`.

---

## API surface

OpenAPI at **`/docs`**.

| Method | Path | Purpose |
|---|---|---|
| POST/GET/DELETE | `/api/sessions[/{id}]` | Session CRUD + messages |
| POST | `/api/chat` | Start a run (202 + run_id); clarify answers resume the parked run |
| GET | `/api/runs/{id}/events` | Live SSE trace |
| GET | `/api/runs/{id}` · `/api/runs/{id}/traces` | Run detail · trace replay |
| GET | `/api/runs/{id}/tool-results/{rid}` | Full tool payload drill-down |
| POST | `/api/runs/{id}/approve` | HITL approval for sensitive tools |
| GET | `/api/agents` · `/api/mcp/health` | Registry · MCP health |
| GET | `/api/health/live` · `/api/health/ready` | Liveness · readiness |

---

## Design decisions

- **LangGraph over LangFlow** — per-request dynamic DAGs; canvas tools bake static flows at design time.
- **MCP Manager as single boundary** — agents never see transports/retries; `langchain.mcp` adapter deliberately not used.
- **Planner assigns WHO/WHAT; workers choose HOW** — ReAct loop + YAML `system_prompt`; code guards (coverage, harvest, retries) compensate for weaker models.
- **Compact hop previews** — worker context uses digest/previews (8-hop window); full payloads via `tool_results` drill-down.
- **HITL clarify + approval** — ambiguous goals park for user input; writes park for explicit approval; clarify resume re-plans with enriched goal.
- **Episodic memory carries plans, never answers** — 4-layer staleness defense keeps fresh-data questions honest.
- **MockLLM as first-class provider** — keyless tests share deterministic behavior with the architecture.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for diagrams, memory layers, MCP, and HITL flows. Requirement mapping: [`docs/REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md).

---

## Known limitations

### LLM model (`z-ai/glm-5.3-flash`)

This submission runs on **GLM-5.3-Flash** through CommandCode — a **normal, cost-efficient flash model**, not a frontier reasoning model (e.g. o1-class or GPT-4.1-level agents). That choice is intentional for demo cost and latency, but it affects live behavior:

| Area | Impact |
|---|---|
| **Tool calling** | Shallow multi-hop ReAct; may stop early, pick a suboptimal tool, or need clarify when scope is vague |
| **Planning** | Plans can be minimal or occasionally ask clarify even when conversation context is enough |
| **Argument filling** | Project keys, sprint ids, and JQL args are sometimes wrong or incomplete — entity coverage gates and harvest hints mitigate but do not eliminate this |
| **Analysis steps** | `data-scientist` sandbox code quality depends on the same model; forecasts are simple statistics, not ML |
| **Reasoning effort** | `LLM_REASONING_EFFORT_STANDARD=high` helps but cannot match deep-reasoning models |

The **architecture** (planner DAG, YAML specialists, MCP boundary, coverage gates, HITL, trace) is model-agnostic. Point `OPENAI_MODEL` / `OPENAI_BASE_URL` at a stronger model to improve tool depth and plan quality without code changes.

### Platform / demo scope

- **MockLLM** (pytest) is scripted intelligence, not real reasoning.
- **Single-node demo**: background runs in-process (`asyncio`); production → queue + Redis pub/sub for SSE.
- **Single-user identity** (`user_id="demo"`); optional `API_TOKEN` only — not full AuthN/AuthZ.
- **Hash embeddings** when `VOYAGE_API_KEY` is unset — semantic memory is weak; Voyage activates real embeddings.
- **Sandbox** = process containment, not OS isolation (microVM is the documented upgrade).
- **Jira MCP** uses an in-memory simulator — realistic tool surface, not Atlassian Cloud latency or auth.

### Roadmap

Real AuthN/Z, queue workers, object storage for large tool payloads, OTel pipeline, multi-tenancy, and optional bootstrap paths for deterministic fetch-heavy reads on weak models.
