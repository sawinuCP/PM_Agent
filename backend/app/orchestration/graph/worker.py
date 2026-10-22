"""Worker — ReAct loop over agent-bound MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.agents.factory import AgentFactory, SpawnedAgent
from app.ai.cost import Usage, cost_of, estimate_usage
from app.ai.prompts import WORKER_NATIVE_SYSTEM, WORKER_REACT_SYSTEM
from app.ai.provider import ToolCallRequest
from app.ai.tool_specs import build_openai_tools, shortlist_tool_names
from app.core.settings import get_settings
from app.mcp_integration.manager import MCPManager
from app.models.plan import PlanStep, WorkerDecision
from app.observability.context_trace import emit_context_snapshot
from app.observability.events import EventType, make_event
from app.orchestration.coverage import finish_blocked_reason
from app.orchestration.hitl import approval_required
from app.orchestration.state import OrchestratorState
from app.orchestration.tooling.arg_enrich import enrich_tool_args
from app.orchestration.tooling.tool_chains import discovery_tools_for_task
from app.orchestration.tooling.hop_evidence import (
    build_tool_hop,
    hop_message_content,
    prior_hops_for_worker,
)
from app.orchestration.tooling.tool_harvest import enrich_worker_body
from app.orchestration.tooling.tool_retry import (
    error_attempt_count,
    finish_blocked_on_tool_errors,
    suggest_recovery_calls,
    unresolved_tool_errors,
)
from app.orchestration.tooling.tool_validate import validate_tool_call
from app.orchestration.utils.hop_utils import ok_hops
from app.orchestration.utils.schema_utils import required_schema_fields

logger = logging.getLogger(__name__)

_FETCH_HEAVY_AGENTS = frozenset({"jira-analyst"})


def _offer_all_tools(agent_name: str, bound_names: list[str]) -> bool:
    """Specialists with large allowlists get every bound tool (planner + YAML decide)."""
    return agent_name in _FETCH_HEAVY_AGENTS or len(bound_names) <= get_settings().worker_tool_shortlist_max


def max_tool_rounds_for(agent: SpawnedAgent) -> int:
    settings = get_settings()
    if agent.template.name in _FETCH_HEAVY_AGENTS:
        return settings.worker_max_tool_rounds_analyst
    return settings.worker_max_tool_rounds


def _catalog_by_name(manager: MCPManager) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for catalog in manager.tool_catalog().values():
        for item in catalog:
            name = item.get("name")
            if isinstance(name, str):
                by_name[name] = item
    return by_name


def _required_args(schema: dict[str, Any] | None) -> list[str]:
    return required_schema_fields(schema, fallback_limit=6)[:8]


def _tool_cards(manager: MCPManager, tool_names: list[str]) -> list[dict[str, Any]]:
    by_name = _catalog_by_name(manager)
    cards: list[dict[str, Any]] = []
    for name in tool_names:
        entry = by_name.get(name, {})
        schema = entry.get("input_schema")
        cards.append({
            "name": name,
            "description": str(entry.get("description") or name)[:200],
            "required_args": _required_args(schema if isinstance(schema, dict) else None),
        })
    return cards


def _shortlisted_names(
    manager: MCPManager,
    bound_names: list[str],
    *,
    agent_name: str,
    goal: str,
    task: str,
    hops: list[dict[str, Any]],
) -> list[str]:
    if _offer_all_tools(agent_name, bound_names):
        return list(bound_names)
    settings = get_settings()
    catalog = _catalog_by_name(manager)
    completed = _successful_tool_names(hops)
    pinned = {str(h.get("tool") or "") for h in hops if h.get("type") == "tool_result"}
    pinned.discard("")
    pinned |= discovery_tools_for_task(task, completed)
    return shortlist_tool_names(
        bound_names,
        catalog,
        goal=goal,
        task=task,
        max_tools=settings.worker_tool_shortlist_max,
        pinned=pinned,
    )


def _worker_body_dict(
    *,
    goal: str,
    task: str,
    conversation: str,
    hops: list[dict[str, Any]],
    is_terminal_step: bool,
    session_context: dict[str, Any] | None = None,
    tools_offered: list[str] | None = None,
    bound_tools: set[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "assigned_task": task,
        "user_goal": goal,
        "conversation_excerpt": (conversation or "")[-4000:],
        "prior_hops": prior_hops_for_worker(hops),
        "terminal_step": is_terminal_step,
    }
    if session_context:
        body["session_context"] = session_context
    if tools_offered is not None:
        body["tools_offered"] = tools_offered
    enrich_worker_body(body, hops=hops, task=task, bound_tools=bound_tools)
    return body


def _worker_payload(
    *,
    goal: str,
    task: str,
    conversation: str,
    hops: list[dict[str, Any]],
    is_terminal_step: bool,
    session_context: dict[str, Any] | None = None,
    tools_offered: list[str] | None = None,
    bound_tools: set[str] | None = None,
) -> str:
    return json.dumps(
        _worker_body_dict(
            goal=goal, task=task, conversation=conversation, hops=hops,
            is_terminal_step=is_terminal_step, session_context=session_context,
            tools_offered=tools_offered, bound_tools=bound_tools,
        ),
        default=str,
    )


def _call_signature(tool: str, args: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)


def _successful_tool_names(hops: list[dict[str, Any]]) -> set[str]:
    return {
        str(hop.get("tool") or "")
        for hop in hops
        if hop.get("type") == "tool_result" and hop.get("status") == "ok"
    }


def _tool_call_block_reason(
    tool_name: str,
    args: dict[str, Any],
    seen_calls: set[str],
    hops: list[dict[str, Any]],
) -> str | None:
    """Return a block reason, or None if the call may proceed."""
    sig = _call_signature(tool_name, args)
    settings = get_settings()
    if sig in seen_calls:
        return "duplicate call blocked (same tool and args already succeeded)"
    failures = error_attempt_count(hops, tool_name, args)
    if failures >= settings.worker_tool_error_retries:
        return "duplicate call blocked (same tool and args after retries exhausted)"
    return None


def _reject_hop(tool_name: str, reason: str) -> dict[str, Any]:
    return {
        "type": "reject",
        "tool": tool_name,
        "status": "rejected",
        "reason": reason,
    }


def _append_native_feedback(
    messages: list[dict[str, Any]],
    *,
    reason: str,
    hops: list[dict[str, Any]],
) -> None:
    messages.append({
        "role": "user",
        "content": json.dumps({
            "error": reason,
            "instruction": (
                "Adjust args (try batch projects/last_n) or pick a different tool. "
                "Do not repeat identical args."
            ),
            "prior_hops": prior_hops_for_worker(hops),
        }, default=str),
    })


def _append_tool_error_feedback(
    messages: list[dict[str, Any]],
    *,
    hops: list[dict[str, Any]],
) -> None:
    errors = unresolved_tool_errors(hops)
    if not errors:
        return
    failures: list[dict[str, Any]] = []
    recovery: list[dict[str, Any]] = []
    for hop in errors[:6]:
        args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
        data = hop.get("data") if isinstance(hop.get("data"), dict) else {}
        failures.append({
            "tool": hop.get("tool"),
            "args": args,
            "error": data.get("error") or hop.get("digest"),
        })
        recovery.extend(suggest_recovery_calls(hop))
    messages.append({
        "role": "user",
        "content": json.dumps({
            "error": "one or more tool calls failed",
            "failures": failures,
            "suggested_recovery": recovery[:4],
            "instruction": (
                "Fix missing/invalid args from the error text and retry, or call a "
                "discovery tool first (e.g. jira_get_releases before "
                "jira_get_release_status). Do not finish while critical errors "
                "remain unresolved."
            ),
            "prior_hops": prior_hops_for_worker(hops),
        }, default=str),
    })


def _finish_block_reason(
    task: str,
    hops: list[dict[str, Any]],
    *,
    goal: str = "",
    bound_tools: set[str] | None = None,
) -> str | None:
    return (
        finish_blocked_reason(task, hops, goal=goal, bound_tools=bound_tools)
        or finish_blocked_on_tool_errors(hops)
    )


def _append_coverage_feedback(
    messages: list[dict[str, Any]],
    *,
    reason: str,
    hops: list[dict[str, Any]],
) -> None:
    messages.append({
        "role": "user",
        "content": json.dumps({
            "error": f"coverage incomplete: {reason}",
            "instruction": (
                "Do not finish yet. Use your role instructions and assigned task "
                "to fetch the missing scope with appropriate tools."
            ),
            "prior_hops": prior_hops_for_worker(hops),
        }, default=str),
    })


def _parallel_safe(tool_name: str, *, agent: SpawnedAgent, manager: MCPManager) -> bool:
    return not approval_required(tool_name, agent=agent.template, manager=manager)


def _prioritize_tool_calls(planned: list[Any], **_: Any) -> list[Any]:
    """Preserve LLM tool-call order — no keyword-based reordering."""
    return list(planned)


async def _execute_native_tool_calls(
    tool_calls: list[Any],
    *,
    raw_message: dict[str, Any] | None,
    bound_names: list[str],
    seen_calls: set[str],
    hops: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    agent: SpawnedAgent,
    manager: MCPManager,
    record_call: Any,
    task: str = "",
    goal: str = "",
) -> bool:
    """Run one LLM tool-call wave; parallelize independent reads. Returns True if any ran."""
    planned: list[ToolCallRequest] = []
    for index, tc in enumerate(tool_calls):
        name = str(getattr(tc, "name", "") or "")
        args = dict(getattr(tc, "args", None) or {})
        if name not in bound_names:
            hops.append(_reject_hop(name, "tool not in agent allowlist"))
            _append_native_feedback(
                messages, reason=f"tool '{name}' not in allowlist", hops=hops)
            continue
        block = _tool_call_block_reason(name, args, seen_calls, hops)
        if block:
            hops.append(_reject_hop(name, block))
            _append_native_feedback(messages, reason=block, hops=hops)
            continue
        planned.append(ToolCallRequest(
            id=str(getattr(tc, "id", "") or f"call-{index}"),
            name=name,
            args=args,
        ))

    if not planned:
        return False

    max_wave = get_settings().worker_max_tools_per_wave
    planned = _prioritize_tool_calls(planned)
    if len(planned) > max_wave:
        for dropped in planned[max_wave:]:
            hops.append(_reject_hop(
                dropped.name,
                f"wave cap ({max_wave}) — executing highest-priority tools first",
            ))
        if messages:
            _append_native_feedback(
                messages,
                reason=(
                    f"Too many parallel tools ({len(planned)}); "
                    f"executing top {max_wave} by task relevance"
                ),
                hops=hops,
            )
        planned = planned[:max_wave]

    parallel_reads = [
        call for call in planned
        if _parallel_safe(call.name, agent=agent, manager=manager)
    ]
    serial_calls = [
        call for call in planned
        if not _parallel_safe(call.name, agent=agent, manager=manager)
    ]

    async def _one(call: ToolCallRequest, call_mode: str) -> tuple[ToolCallRequest, dict[str, Any]]:
        hop = await record_call(call.name, call.args, mode=call_mode)
        if hop.get("status") == "ok":
            seen_calls.add(_call_signature(call.name, call.args))
        return call, hop

    executed: list[tuple[ToolCallRequest, dict[str, Any]]] = []
    if len(parallel_reads) > 1:
        executed.extend(await asyncio.gather(*[
            _one(call, "parallel") for call in parallel_reads
        ]))
    elif len(parallel_reads) == 1:
        executed.append(await _one(parallel_reads[0], ""))

    for call in serial_calls:
        executed.append(await _one(call, ""))

    if messages:
        for call, hop in executed:
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": hop_message_content(hop),
            })
        if any(hop.get("status") != "ok" for _, hop in executed):
            _append_tool_error_feedback(messages, hops=hops)
    return bool(executed)


async def _structured_decision_round(
    *,
    agent: SpawnedAgent,
    system: str,
    goal: str,
    task: str,
    conversation: str,
    hops: list[dict[str, Any]],
    is_terminal_step: bool,
    session_context: dict[str, Any],
    cards: list[dict[str, Any]],
    bundle: Any,
    step_id: str,
) -> WorkerDecision | None:
    user_body = _worker_body_dict(
        goal=goal,
        task=task,
        conversation=conversation,
        hops=hops,
        is_terminal_step=is_terminal_step,
        session_context=session_context or {},
    )
    user_body["bound_tools"] = cards
    user = json.dumps(user_body, default=str)
    try:
        await _emit_llm_started(
            bundle, step_id, agent,
            label=f"{agent.template.name} is analyzing evidence and choosing tools…",
            input_preview=user[:6000],
        )
        decision, _usage = await agent.provider.generate_structured(
            WORKER_REACT_SYSTEM,
            f"{system}\n\nREQUEST:\n{user}",
            WorkerDecision,
            tier="standard",
        )
        await emit_usage(
            bundle, step_id, agent.provider.last_usage(),
            agent.provider.usage_model(), provider=agent.provider)
        return decision
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker structured round failed step=%s: %s", step_id, exc)
        return None


async def execute_worker_step(
    *,
    step: PlanStep,
    agent: SpawnedAgent,
    state: OrchestratorState,
    bundle: Any,
    factory: AgentFactory,
    manager: MCPManager,
    run_id: str,
    is_terminal_step: bool = False,
) -> dict[str, Any]:
    if bundle.emit is not None:
        await bundle.emit(make_event(EventType.NODE_STARTED, bundle.run_id, node=step.id))
    step_started = time.perf_counter()
    # LangGraph schedules sibling steps together; yield so parallel roots emit before either finishes.
    await asyncio.sleep(0)

    goal = state.get("goal", "")
    conversation = state.get("conversation", "")
    session_context = state.get("session_context") or {}
    step_outputs = state.get("step_outputs", {})
    upstream = {
        dep: step_outputs[dep]
        for dep in step.depends_on
        if dep in step_outputs
    }
    missing_upstream = [dep for dep in step.depends_on if dep not in step_outputs]
    bound_names = [tool.name for tool in agent.tools]
    bound_tool_set = set(bound_names)
    settings = get_settings()
    use_native = settings.worker_native_tools
    hops: list[dict[str, Any]] = []
    tool_refs: list[str] = []
    seen_calls: set[str] = set()
    final_answer: str | None = None

    system = factory.build_system_prompt(agent, upstream)
    offered: list[str] = []
    openai_tools: list[dict[str, Any]] = []

    if use_native:
        offered = _shortlisted_names(
            manager, bound_names,
            agent_name=agent.template.name,
            goal=goal, task=step.task, hops=hops)
        openai_tools = build_openai_tools(manager, offered)
        if len(offered) < len(bound_names):
            logger.info(
                "worker step=%s shortlist %d/%d tools for native API",
                step.id, len(offered), len(bound_names))

    worker_payload = _worker_payload(
        goal=goal, task=step.task, conversation=conversation, hops=hops,
        is_terminal_step=is_terminal_step, session_context=session_context,
        bound_tools=bound_tool_set)
    if missing_upstream:
        worker_payload = json.dumps({
            **json.loads(worker_payload),
            "upstream_wait": (
                f"Required upstream steps not ready: {missing_upstream}. "
                "Do not synthesize — this should not happen; report incomplete deps."
            ),
        }, default=str)

    if bundle.emit is not None:
        await emit_context_snapshot(
            bundle.emit, bundle.run_id, step.id,
            conversation=conversation,
            prompt_bindings={
                "tool_execution": "mcp",
                "llm_tool_binding": "openai_tools_api",
                "tools_registered": len(offered) if use_native else 0,
                "tools_allowlist_count": len(bound_names),
            },
            worker_prompt=worker_payload,
            upstream={"session_context": session_context} if session_context else None,
        )

    async def _record_call(
        tool_name: str, args: dict[str, Any], *, mode: str = "",
    ) -> dict[str, Any]:
        args = enrich_tool_args(tool_name, args, hops, task=step.task)
        schema = _catalog_by_name(manager).get(tool_name, {}).get("input_schema")
        validation = validate_tool_call(
            tool_name,
            args,
            schema=schema if isinstance(schema, dict) else None,
            task=step.task,
            goal=goal,
        )
        if validation:
            hop = _reject_hop(tool_name, f"validation: {validation}")
            hops.append(hop)
            return hop
        if approval_required(tool_name, agent=agent.template, manager=manager):
            approved = await bundle.request_approval(tool_name)
            if not approved:
                hops.append(_reject_hop(tool_name, "human rejected"))
                return hops[-1]
        outcome = await manager.call_tool_for_agent(
            tool_name=tool_name, args=args,
            agent_template=agent.template, step_id=step.id,
            run_id=state.get("run_id", run_id), emit=bundle.emit, mode=mode)
        tool_refs.append(outcome["result_id"])
        hop = build_tool_hop(tool_name=tool_name, args=args, outcome=outcome)
        hops.append(hop)
        return hop

    _invoke = _record_call

    summary = f"No tool calls completed for: {step.task[:180]}"
    native_handled = False

    stop_loop = False
    native_failed = (
        is_terminal_step and use_native and openai_tools and not native_handled
    )
    messages: list[dict[str, Any]] = []

    if not native_handled and use_native and openai_tools:
        messages = [
            {"role": "system", "content": f"{system}\n\n{WORKER_NATIVE_SYSTEM}"},
            {"role": "user", "content": worker_payload},
        ]

    for _round in range(max_tool_rounds_for(agent)):
        if stop_loop or native_handled:
            break

        decision: WorkerDecision | None = None
        if use_native and not native_failed and openai_tools:
            messages[1]["content"] = _worker_payload(
                goal=goal, task=step.task, conversation=conversation, hops=hops,
                is_terminal_step=is_terminal_step, session_context=session_context,
                tools_offered=offered, bound_tools=bound_tool_set)
            try:
                await _emit_llm_started(
                    bundle, step.id, agent,
                    label=f"{agent.template.name} is reasoning about the next action…",
                    input_preview=messages[1]["content"][:6000]
                    if len(messages) > 1 and isinstance(messages[1].get("content"), str)
                    else "",
                )
                completion = await agent.provider.generate_with_tools(
                    messages, openai_tools, tier="standard")
                await emit_usage(
                    bundle, step.id, agent.provider.last_usage(),
                    agent.provider.usage_model(), provider=agent.provider)
                if completion.tool_calls:
                    if completion.raw_message:
                        messages.append(completion.raw_message)
                    ran = await _execute_native_tool_calls(
                        completion.tool_calls,
                        raw_message=completion.raw_message,
                        bound_names=bound_names,
                        seen_calls=seen_calls,
                        hops=hops,
                        messages=messages,
                        agent=agent,
                        manager=manager,
                        record_call=_record_call,
                        task=step.task,
                        goal=goal,
                    )
                    if ran:
                        if any(
                            hop.get("type") == "reject"
                            and hop.get("reason") == "human rejected"
                            for hop in hops[-len(completion.tool_calls):]
                        ):
                            stop_loop = True
                        continue
                else:
                    text = (completion.content or "").strip()
                    block = _finish_block_reason(
                        step.task, hops, goal=goal, bound_tools=bound_tool_set)
                    if block:
                        hops.append(_reject_hop("", f"coverage: {block}"))
                        if messages:
                            _append_coverage_feedback(
                                messages, reason=block, hops=hops)
                        continue
                    decision = WorkerDecision(
                        action="finish",
                        summary=text or "Completed using retrieved tool data.")
                    if is_terminal_step and text:
                        final_answer = text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "native tool round failed step=%s (%s); falling back to JSON",
                    step.id, exc)
                native_failed = True
                use_native = False

        if decision is None:
            cards = _tool_cards(
                manager,
                _shortlisted_names(
                    manager, bound_names,
                    agent_name=agent.template.name,
                    goal=goal, task=step.task, hops=hops)
                if not native_failed else bound_names)
            decision = await _structured_decision_round(
                agent=agent,
                system=system,
                goal=goal,
                task=step.task,
                conversation=conversation,
                hops=hops,
                is_terminal_step=is_terminal_step,
                session_context=session_context,
                cards=cards,
                bundle=bundle,
                step_id=step.id,
            )
            if decision is None:
                break

        if decision.action == "finish":
            block = _finish_block_reason(
                step.task, hops, goal=goal, bound_tools=bound_tool_set)
            if block:
                hops.append(_reject_hop("", f"coverage: {block}"))
                if use_native and not native_failed and messages:
                    _append_coverage_feedback(
                        messages, reason=block, hops=hops)
                continue
            summary = (decision.summary or summary)[:500]
            if is_terminal_step:
                final_answer = (decision.summary or summary).strip()
            break

        if decision.action != "call_tool" or not decision.tool:
            continue

        tool_name = decision.tool
        args = dict(decision.args or {})
        if tool_name not in bound_names:
            hops.append(_reject_hop(tool_name, "tool not in agent allowlist"))
            if use_native and not native_failed:
                _append_native_feedback(
                    messages, reason=f"tool '{tool_name}' not in allowlist",
                    hops=hops)
            continue

        block = _tool_call_block_reason(tool_name, args, seen_calls, hops)
        if block:
            hops.append(_reject_hop(tool_name, block))
            if use_native and not native_failed and messages:
                _append_native_feedback(messages, reason=block, hops=hops)
            continue

        hop = await _record_call(tool_name, args)

        if hop.get("type") == "reject" and hop.get("reason") == "human rejected":
            summary = f"User rejected the requested action ({tool_name})."
            if is_terminal_step:
                final_answer = (
                    f"I wasn't able to complete that — approval for `{tool_name}` "
                    "was declined.")
            stop_loop = True
            continue

        if hop.get("status") == "ok":
            seen_calls.add(_call_signature(tool_name, args))

        if use_native and not native_failed and messages:
            messages.append({
                "role": "tool",
                "tool_call_id": f"legacy-{len(hops)}",
                "content": hop_message_content(hop),
            })
            if hop.get("status") != "ok":
                _append_tool_error_feedback(messages, hops=hops)
            continue

    if hops and summary.startswith("No tool calls"):
        summary = f"Completed {len(hops)} hop(s) for: {step.task[:180]}"

    if bundle.emit is not None:
        step_ms = int((time.perf_counter() - step_started) * 1000)
        await bundle.emit(make_event(
            EventType.NODE_FINISHED, bundle.run_id,
            node=step.id, summary=f"{summary[:280]} [{step_ms}ms]"))

    packet = {"type": "step_result", "step": step.id, "summary": summary, "hops": hops}
    result: dict[str, Any] = {
        "step_outputs": {step.id: json.dumps(packet, default=str)},
        "tool_result_refs": {step.id: tool_refs},
    }
    if final_answer:
        result["final_answer"] = final_answer
    return result


async def _emit_llm_started(
    bundle: Any,
    node: str,
    agent: SpawnedAgent,
    *,
    label: str,
    input_preview: str = "",
) -> None:
    if bundle.emit is None:
        return
    await bundle.emit(make_event(
        EventType.LLM_CALL_STARTED, bundle.run_id,
        node=node, label=label, input_preview=input_preview[:6000]))


async def emit_usage(bundle: Any, node: str, usage: Any,
                     model: str, *, provider: Any = None,
                     allow_empty: bool = False) -> None:
    if bundle.emit is None:
        return
    usage = usage if isinstance(usage, Usage) else Usage()
    if usage.total_tokens == 0:
        if not allow_empty:
            return
        usage = estimate_usage("synthesis reply", "answer")
    cost = cost_of(usage, model) if usage.total_tokens else 0.0
    visible = usage.visible_completion_tokens or max(
        0, usage.completion_tokens - usage.reasoning_tokens)
    input_preview, output_preview = provider.io_preview() if provider else ("", "")
    await bundle.emit(make_event(
        EventType.TOKEN_USAGE, bundle.run_id, node=node,
        prompt=usage.prompt_tokens,
        completion=usage.completion_tokens,
        reasoning=usage.reasoning_tokens,
        visible_completion=visible,
        cost_usd=round(cost, 6), model=model, kind="llm",
        input_preview=input_preview, output_preview=output_preview))
