# Requirements traceability

| Requirement | Implementation | Verification |
|---|---|---|
| Config-driven multi-agent orchestration | `backend/config/agents/*.yaml` + `AgentRegistry` + `AgentFactory.spawn` | `test_registry_digest.py`, `test_planner.py` |
| Dynamic DAG per request | `planner.py` → `Plan` → `graph/builder.py` (LangGraph) | `test_executor_loop.py`, `test_plan_validation.py` |
| Agent-owned tool choice | `graph/worker.py` ReAct + YAML `allowed_tools` | `test_worker_tool_dedup.py`, `test_tool_chains.py` |
| MCP as sole tool boundary | `mcp_integration/manager.py` | `test_jira_pm_tools.py`, `e2e_smoke.py` |
| Parallel execution | Intra-step `asyncio.gather`; inter-step parallel DAG roots | `test_plan_deps.py`, `test_performance_optimizations.py` |
| Fetch completeness | `coverage/entity_coverage.py`, `tool_harvest.py` | `test_coverage.py`, `test_entity_validation.py` |
| HITL for sensitive writes | `orchestration/hitl/` + approval API | `test_hitl.py` |
| Session memory L1–L4 | `agents/worker.py`, `memory/` | `test_memory_runtime.py`, `test_conversation_memory.py` |
| Live trace + SSE | `observability/` + `GET /api/runs/{id}/events` | `test_api_surface.py`, frontend `client.test.ts` |
| Graceful degradation | Tool failures → digest hops; MCP down → unavailable handler | `test_tool_retry.py`, integration tests |
| OpenAPI / REST API | FastAPI routes in `app/api/` | `/docs`, `test_api_surface.py` |
| UI trace viewer | `frontend/src/` chat + trace + graph | `npm test`, manual demo |
| Environment configuration | `backend/.env.example`, `core/settings.py` | Documented in README |
| Docker deployment | `docker-compose.yml` | `docker compose up --build` |
