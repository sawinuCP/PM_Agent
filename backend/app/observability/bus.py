"""In-process event bus fanning out to SSE, trace persistence, and logs.

Trace rows are buffered per run and flushed in batches (see
``trace_write_batch_size``) to reduce DB connection churn during high-event runs.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
from collections import defaultdict
from typing import Any

from app.ai.cost import Usage, cost_of, estimate_usage
from app.core.settings import get_settings
from app.models import db
from app.observability.context_trace import build_context_sections
from app.observability.events import BaseEvent, EventType, make_event, serialize_event

logger = logging.getLogger(__name__)

_MAX_QUEUE = 500
_FORCE_FLUSH_TYPES = frozenset({
    EventType.RUN_FINISHED,
    EventType.RUN_FAILED,
    EventType.CLARIFY_REQUIRED,
    EventType.APPROVAL_REQUIRED,
})


class EventBus:
    """Fan-out event hub keyed by run_id (single-node; Redis pub/sub at scale)."""

    def __init__(self, *, trace_batch_size: int | None = None) -> None:
        settings = get_settings()
        self._trace_batch_size = max(
            1,
            trace_batch_size if trace_batch_size is not None
            else settings.trace_write_batch_size,
        )
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._counters: dict[str, itertools.count] = defaultdict(itertools.count)
        self._usage: dict[str, dict[str, float | int]] = defaultdict(self._empty_usage)
        self._lock = asyncio.Lock()
        self._trace_buffers: dict[str, list[db.TraceWriteRow]] = defaultdict(list)
        self._trace_buffer_lock = asyncio.Lock()
        self._terminal_runs: set[str] = set()

    @staticmethod
    def _empty_usage() -> dict[str, float | int]:
        return {
            "tokens": 0, "prompt": 0, "completion": 0, "reasoning": 0,
            "visible_completion": 0, "cost_usd": 0.0, "calls": 0,
            "embed_tokens": 0, "embed_cost_usd": 0.0, "embed_calls": 0, "embed_ms": 0,
        }

    def usage_totals(self, run_id: str) -> dict[str, float | int]:
        """Accumulated token_usage events for a run (LLM calls, tokens, cost)."""
        totals = self._usage.get(run_id)
        if not totals:
            return self._empty_usage()
        return {
            "tokens": int(totals["tokens"]),
            "prompt": int(totals["prompt"]),
            "completion": int(totals["completion"]),
            "reasoning": int(totals.get("reasoning", 0)),
            "visible_completion": int(totals.get("visible_completion", 0)),
            "cost_usd": round(float(totals["cost_usd"]), 6),
            "calls": int(totals["calls"]),
            "embed_tokens": int(totals.get("embed_tokens", 0)),
            "embed_cost_usd": round(float(totals.get("embed_cost_usd", 0)), 6),
            "embed_calls": int(totals.get("embed_calls", 0)),
            "embed_ms": int(totals.get("embed_ms", 0)),
        }

    def _record_usage(self, event: BaseEvent) -> None:
        bucket = self._usage[event.run_id]
        if event.type == EventType.EMBEDDING_USAGE:
            tokens = int(getattr(event, "tokens", 0) or 0)
            cost = float(getattr(event, "cost_usd", 0) or 0)
            bucket["embed_tokens"] = int(bucket.get("embed_tokens", 0)) + tokens
            bucket["embed_cost_usd"] = float(bucket.get("embed_cost_usd", 0)) + cost
            bucket["embed_calls"] = int(bucket.get("embed_calls", 0)) + 1
            bucket["embed_ms"] = int(bucket.get("embed_ms", 0)) + int(getattr(event, "ms", 0) or 0)
            bucket["cost_usd"] = float(bucket["cost_usd"]) + cost
            return
        if event.type != EventType.TOKEN_USAGE:
            return
        prompt = int(getattr(event, "prompt", 0) or 0)
        completion = int(getattr(event, "completion", 0) or 0)
        reasoning = int(getattr(event, "reasoning", 0) or 0)
        visible = int(getattr(event, "visible_completion", 0) or 0)
        bucket["prompt"] = int(bucket["prompt"]) + prompt
        bucket["completion"] = int(bucket["completion"]) + completion
        bucket["reasoning"] = int(bucket.get("reasoning", 0)) + reasoning
        bucket["visible_completion"] = int(bucket.get("visible_completion", 0)) + visible
        bucket["tokens"] = int(bucket["tokens"]) + prompt + completion
        bucket["cost_usd"] = float(bucket["cost_usd"]) + float(getattr(event, "cost_usd", 0) or 0)
        bucket["calls"] = int(bucket["calls"]) + 1

    async def _enqueue_trace(
        self,
        *,
        run_id: str,
        seq: int,
        event: BaseEvent,
        payload: dict[str, Any],
    ) -> None:
        row: db.TraceWriteRow = (
            run_id,
            seq,
            event.type.value,
            getattr(event, "agent", None) or getattr(event, "node", None),
            payload,
        )
        flush = False
        async with self._trace_buffer_lock:
            buffer = self._trace_buffers[run_id]
            buffer.append(row)
            if event.type in _FORCE_FLUSH_TYPES:
                if event.type in {EventType.RUN_FINISHED, EventType.RUN_FAILED}:
                    self._terminal_runs.add(run_id)
                flush = True
            elif run_id in self._terminal_runs or event.type == EventType.EMBEDDING_USAGE or len(buffer) >= self._trace_batch_size:
                flush = True
        if flush:
            await self.flush_traces(run_id)

    async def flush_traces(self, run_id: str) -> None:
        """Write buffered trace rows for one run (no-op when empty)."""
        async with self._trace_buffer_lock:
            batch = self._trace_buffers.pop(run_id, [])
        if not batch:
            return
        try:
            await db.save_traces_batch(batch)
        except Exception:  # noqa: BLE001 - trace persistence must never kill a run
            logger.exception("trace batch persistence failed for run=%s", run_id)
            async with self._trace_buffer_lock:
                pending = self._trace_buffers.get(run_id, [])
                self._trace_buffers[run_id] = batch + pending

    async def flush_all_traces(self) -> None:
        """Flush every buffered run (platform shutdown)."""
        async with self._trace_buffer_lock:
            run_ids = list(self._trace_buffers.keys())
        for run_id in run_ids:
            await self.flush_traces(run_id)
        self._terminal_runs.clear()

    async def emit(self, event: BaseEvent) -> dict[str, Any]:
        """Assign sequence, persist to the trace store, fan out to SSE queues."""
        payload = serialize_event(event)
        async with self._lock:
            payload["seq"] = next(self._counters[event.run_id])
            self._record_usage(event)
        # Streaming tokens are live-only — persist a single finished answer instead.
        persist = event.type != EventType.ANSWER_DELTA
        if persist:
            await self._enqueue_trace(
                run_id=event.run_id,
                seq=payload["seq"],
                event=event,
                payload=payload,
            )
        for queue in list(self._subscribers.get(event.run_id, [])):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE queue full; dropping event for run=%s", event.run_id)
        logger.debug("event %s seq=%s run=%s", event.type.value, payload["seq"], event.run_id)
        return payload

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(run_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(run_id, None)


    async def emit_token_usage(self, run_id: str, *, node: str, usage: Any,
                               model: str, reasoning_effort: str = "",
                               allow_empty: bool = False,
                               input_preview: str = "",
                               output_preview: str = "",
                               ms: int | None = None) -> dict[str, Any] | None:
        recorded = usage if isinstance(usage, Usage) else Usage()
        if recorded.total_tokens == 0:
            if allow_empty:
                recorded = estimate_usage("planner structured output", "plan json")
            else:
                return None
        cost = cost_of(recorded, model) if recorded.total_tokens else 0.0
        visible = recorded.visible_completion_tokens or max(
            0, recorded.completion_tokens - recorded.reasoning_tokens)
        return await self.emit(make_event(
            EventType.TOKEN_USAGE, run_id, node=node,
            prompt=recorded.prompt_tokens,
            completion=recorded.completion_tokens,
            reasoning=recorded.reasoning_tokens,
            visible_completion=visible,
            cost_usd=round(cost, 6), model=model, kind="llm",
            reasoning_effort=reasoning_effort or "",
            input_preview=input_preview or "",
            output_preview=output_preview or "",
            ms=ms))

    async def emit_llm_call_started(
        self,
        run_id: str,
        *,
        node: str,
        label: str = "",
        input_preview: str = "",
    ) -> dict[str, Any]:
        return await self.emit(make_event(
            EventType.LLM_CALL_STARTED, run_id,
            node=node, label=label or node, input_preview=input_preview or ""))

    async def emit_embedding_calls(
        self, run_id: str, *, node: str, calls: list[Any],
    ) -> list[dict[str, Any]]:
        """Emit one embedding_usage event per captured Voyage/hash call."""
        payloads: list[dict[str, Any]] = []
        for call in calls:
            payloads.append(await self.emit(make_event(
                EventType.EMBEDDING_USAGE, run_id,
                node=node,
                model=getattr(call, "model", ""),
                provider=getattr(call, "provider", ""),
                tokens=int(getattr(call, "tokens", 0) or 0),
                cost_usd=round(float(getattr(call, "cost_usd", 0) or 0), 6),
                ms=int(getattr(call, "ms", 0) or 0),
                input_type=getattr(call, "input_type", "") or "",
                cached=bool(getattr(call, "cached", False)),
            )))
        return payloads

    async def emit_run_started(self, run_id: str, goal: str) -> dict[str, Any]:
        return await self.emit(make_event(EventType.RUN_STARTED, run_id, goal=goal))

    async def emit_router_decision(self, run_id: str, *, intent: str, path: str,
                                   reason: str = "", temporal: bool = False) -> dict[str, Any]:
        return await self.emit(make_event(EventType.ROUTER_DECISION, run_id,
                                          intent=intent, path=path, reason=reason,
                                          temporal_sensitivity=temporal))

    async def emit_plan_created(self, run_id: str, plan: dict[str, Any],
                                hint_used: bool) -> dict[str, Any]:
        return await self.emit(make_event(EventType.PLAN_CREATED, run_id,
                                          plan=plan, hint_used=hint_used))

    async def emit_answer_delta(self, run_id: str, text: str, *,
                                done: bool = False) -> dict[str, Any]:
        return await self.emit(make_event(EventType.ANSWER_DELTA, run_id,
                                          text=text, done=done))

    async def stream_answer(self, run_id: str, answer: str) -> None:
        """Push the final answer in word-sized SSE chunks so the UI can stream it."""
        if not answer:
            return
        pieces = re.findall(r"\S+\s*", answer) or [answer]
        buf = ""
        for index, piece in enumerate(pieces):
            buf += piece
            last = index == len(pieces) - 1
            if last or len(buf) >= 24:
                await self.emit_answer_delta(run_id, buf, done=last)
                buf = ""
                await asyncio.sleep(0.008)
        if buf:
            await self.emit_answer_delta(run_id, buf, done=True)

    async def emit_run_finished(self, run_id: str, result: str,
                                totals: dict[str, Any], *,
                                stream: bool = True) -> dict[str, Any]:
        if stream:
            await self.stream_answer(run_id, result)
        return await self.emit(make_event(EventType.RUN_FINISHED, run_id,
                                          result=result, totals=totals))

    async def emit_run_failed(self, run_id: str, error: str) -> dict[str, Any]:
        return await self.emit(make_event(EventType.RUN_FAILED, run_id, error=error))

    async def emit_clarify_required(
        self, run_id: str, *, question: str, step_id: str = "",
        reason: str = "missing_scope",
    ) -> dict[str, Any]:
        return await self.emit(make_event(
            EventType.CLARIFY_REQUIRED, run_id,
            question=question, step_id=step_id, reason=reason))

    async def emit_context_snapshot(
        self, run_id: str, node: str, /, **fields: Any,
    ) -> dict[str, Any] | None:
        sections = build_context_sections(**fields)
        if not sections:
            return None
        return await self.emit(make_event(
            EventType.CONTEXT_SNAPSHOT, run_id, node=node, sections=sections))
