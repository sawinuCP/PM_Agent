# Test dataset

Manual and automated cases for validating orchestration behavior against the in-memory Jira simulator.

## Automated (keyless)

| Suite | Command | Coverage |
|---|---|---|
| Backend unit + integration | `cd backend && pytest tests -q` | Router, planner, worker, MCP fakes, coverage gates, HITL, memory |
| Frontend | `cd frontend && npm test` | API client, SSE hooks, workflow graph helpers |
| E2E smoke | `python backend/scripts/e2e_smoke.py` | Full loop with real in-process MCP servers |
| E2E scenarios | `python backend/scripts/e2e_scenarios.py` | Scripted multi-step flows |

## Live LLM (requires `LLM_PROVIDER=openai`)

| Script | Purpose |
|---|---|
| `backend/scripts/live_dataset_runner.py` | Runs benchmark prompts, writes `backend/data/live_dataset_results.json` |
| `backend/scripts/live_answer_verify.py` | Checks answers against `ground_truth.py` rules |
| `backend/scripts/live_integration_probe.py` | One cost-capped LLM + Voyage embedding smoke (run deliberately) |

## Representative prompts

| ID | Prompt | Expected behavior |
|---|---|---|
| Q-01 | `Hi, what can you do?` | Fast path, no plan |
| Q-02 | `Predict the velocity for our next sprint based on previous Jira sprints` | Plan → jira-analyst → data-scientist → synthesis |
| Q-03 | `Compare bug counts across the last 3 sprints and analyze their trend` | Multi-hop Jira fetch + analysis |
| Q-04 | `What Jira projects exist and who leads each?` | Project list via jira-analyst |
| Q-05 | `What is the status of PLAT-9?` | Single-issue lookup |
| Q-06 | `Create a Jira issue that tracks the velocity forecast for the next sprint` | HITL approval on write |
| Q-07 | Repeat Q-02 in same session | Episodic plan hint + fresh tool data |

Ground-truth validators live in `backend/scripts/ground_truth.py` (structural checks on tool usage and answer content, not brittle string equality).

## Jira simulator seed

The Jira MCP server uses deterministic seeded data (projects `PLAT`, `NOVA`, etc.). Reset via the `jira_reset_simulator` tool during development if state drifts.
