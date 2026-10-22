"""Deterministic tool-result digest pipeline.

Digests are pure Python (counts, key fields, head/tail samples). The full
payload persists to the ``tool_results`` table; the digest feeds prompts and
trace previews.
"""

from __future__ import annotations

import json
from typing import Any

_HEAD_SAMPLE = 3
_TAIL_SAMPLE = 2
_STRING_SAMPLE_MAX = 120
_NESTED_ITEM_SAMPLE_MAX = 220


def build_digest(payload: Any) -> str:
    """Human-readable digest for agent prompts and trace previews."""
    if isinstance(payload, str):
        return _digest_text(payload)
    if isinstance(payload, dict):
        return _digest_dict(payload)
    if isinstance(payload, list):
        return _digest_list(payload)
    return repr(payload)[:_STRING_SAMPLE_MAX]


def normalize_payload(payload: Any) -> dict[str, Any]:
    """Force every tool result into a JSON object the next LLM can parse."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"items": payload, "count": len(payload)}
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {"text": payload}
        return normalize_payload(parsed)
    return {"value": payload}


def structured_preview(payload: Any, *, max_items: int = 20) -> dict[str, Any]:
    """Compact structured view of a tool payload for the worker / aggregator."""
    data = normalize_payload(payload)
    preview: dict[str, Any] = {}
    priority = (
        "project", "project_name", "overall", "dimensions", "evidence", "assessment",
        "totals", "comparison", "by_sprint", "by_project", "reports", "sprints",
        "sprint_ids", "projects", "board_ids", "versions", "last_n",
        "bug_count", "type_counts", "count", "issues", "sprint", "issue",
        "items", "boards", "releases", "missing", "error",
    )
    keys = [key for key in priority if key in data]
    keys.extend(key for key in data if key not in keys)
    for key in keys[:16]:
        value = data[key]
        if key in {"by_sprint", "by_project", "reports", "sprints", "items",
                   "boards"} and isinstance(value, list):
            preview[key] = [_compact_row(item) for item in value[:20]]
            preview[f"{key}_count"] = len(value)
        elif key == "issues" and isinstance(value, list):
            preview[key] = value[:8]
            preview[f"{key}_count"] = len(value)
        elif isinstance(value, list):
            preview[key] = value[:max_items]
            preview[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            preview[key] = {k: value[k] for k in list(value)[:12]}
        elif isinstance(value, str) and len(value) > 400:
            preview[key] = value[:400]
        else:
            preview[key] = value
    return preview


def _compact_row(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    keep = {
        "id", "sprint_id", "name", "state", "issue_count", "bug_count",
        "type_counts", "completed_story_points", "remaining_story_points",
        "completed_points", "committed_points", "status_counts",
        "completed_points_recorded", "issue_points_done", "issue_points_remaining",
        "assessment_inputs", "sprint",
    }
    row = {key: item[key] for key in keep if key in item}
    if "issues" in item and isinstance(item["issues"], list):
        row["issues"] = item["issues"][:4]
        row["issues_shown"] = min(4, len(item["issues"]))
    return row or {k: item[k] for k in list(item)[:8]}


def loop_record(record: dict[str, Any], *, limit: int = 2400) -> str:
    """One JSON line the worker transcript appends after each hop."""
    return json.dumps(record, default=str, ensure_ascii=False)[:limit]


def decode_step_packet(text: Any, step_id: str = "") -> dict[str, Any]:
    """Parse a worker step_output into a structured packet."""
    if isinstance(text, dict) and text.get("type") == "step_result":
        return text
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("type") == "step_result":
            return parsed
        return {"type": "step_result", "step": step_id, "summary": text, "hops": []}
    return {"type": "step_result", "step": step_id, "summary": str(text), "hops": []}


def decode_upstream(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn stored step_output values into JSON objects for the next hop."""
    return {sid: decode_step_packet(value, sid) for sid, value in raw.items()}


def build_synthesis_evidence(step_outputs: dict[str, str]) -> list[dict[str, Any]]:
    """Structured step evidence for the synthesis node.

    Preserves tool previews and result ids so the aggregator can cite this run's
    data without re-parsing prose summaries.
    """
    evidence: list[dict[str, Any]] = []
    for step_id in sorted(step_outputs.keys()):
        packet = decode_step_packet(step_outputs[step_id], step_id)
        tool_rows: list[dict[str, Any]] = []
        for hop in packet.get("hops") or []:
            if hop.get("type") != "tool_result" or hop.get("status") != "ok":
                continue
            tool_rows.append({
                "tool": hop.get("tool"),
                "result_id": hop.get("result_id"),
                "args": args_digest(hop.get("args") if isinstance(hop.get("args"), dict) else {}),
                "data": structured_preview(hop.get("data") or {}, max_items=12),
            })
        evidence.append({
            "step": packet.get("step") or step_id,
            "summary": packet.get("summary") or "",
            "tool_evidence": tool_rows,
        })
    return evidence


_ROLLUP_KEYS = (
    "project", "project_name", "board_id", "sprint_id", "name", "state",
    "bug_count", "issue_count", "type_counts", "by_sprint", "by_project",
    "comparison", "totals", "reports", "sprints", "last_n",
)


def extract_synthesis_rollup(step_outputs: dict[str, str]) -> dict[str, Any]:
    """Compact cross-step metrics so synthesis survives per-step trimming."""
    by_project: dict[str, dict[str, Any]] = {}
    sprint_rows: list[dict[str, Any]] = []
    seen_sprints: set[str] = set()

    def _merge_project(key: str, payload: dict[str, Any]) -> None:
        slot = by_project.setdefault(key, {"project": key})
        for field in ("bug_count", "issue_count", "open_issues", "overall"):
            if field in payload and payload[field] is not None:
                slot[field] = payload[field]
        if payload.get("type_counts"):
            slot["type_counts"] = payload["type_counts"]

    for step_id in sorted(step_outputs.keys()):
        packet = decode_step_packet(step_outputs[step_id], step_id)
        for hop in packet.get("hops") or []:
            if hop.get("type") != "tool_result" or hop.get("status") != "ok":
                continue
            data = normalize_payload(hop.get("data") or {})
            args = hop.get("args") if isinstance(hop.get("args"), dict) else {}
            project = str(
                data.get("project")
                or args.get("project")
                or args.get("projects")
                or ""
            ).split(",")[0].strip()
            if project:
                _merge_project(project, data)
            for row in data.get("by_sprint") or []:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("sprint_id") or row.get("id") or "")
                dedupe = sid or f"{project}:{row.get('name')}"
                if dedupe in seen_sprints:
                    continue
                seen_sprints.add(dedupe)
                sprint_rows.append(_compact_row(row))
            for row in data.get("sprints") or []:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("sprint_id") or row.get("id") or "")
                if sid in seen_sprints:
                    continue
                seen_sprints.add(sid)
                sprint_rows.append(_compact_row({
                    **row,
                    "sprint_id": sid or row.get("name"),
                }))
            comparison = data.get("comparison")
            if isinstance(comparison, dict):
                bugs = comparison.get("bug_counts")
                names = comparison.get("names") or comparison.get("sprint_ids")
                if isinstance(bugs, list):
                    for idx, count in enumerate(bugs[:12]):
                        label = ""
                        if isinstance(names, list) and idx < len(names):
                            label = str(names[idx])
                        sprint_rows.append({
                            "sprint_id": label or f"sprint_{idx}",
                            "bug_count": count,
                            "source": "comparison",
                        })
                for key, value in comparison.items():
                    if key.endswith("_counts") and isinstance(value, list) and key != "bug_counts":
                        sprint_rows.append({"metric": key, "values": value[:12]})

    rollup: dict[str, Any] = {}
    if by_project:
        rollup["by_project"] = [
            {k: row[k] for k in _ROLLUP_KEYS if k in row}
            for row in by_project.values()
        ]
    if sprint_rows:
        rollup["sprint_metrics"] = sprint_rows[:48]
    return rollup


def pack_synthesis_evidence_json(
    step_outputs: dict[str, str],
    *,
    max_chars: int,
) -> str:
    """Serialize synthesis evidence with rollup-first packing under a char cap."""
    rollup = extract_synthesis_rollup(step_outputs)
    steps = build_synthesis_evidence(step_outputs)
    envelope: dict[str, Any] = {"rollup": rollup, "steps": steps}
    text = json.dumps(envelope, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    compact_steps: list[dict[str, Any]] = []
    for step in steps:
        compact_steps.append({
            "step": step.get("step"),
            "summary": (step.get("summary") or "")[:1200],
            "tool_evidence": [
                {
                    "tool": row.get("tool"),
                    "args": row.get("args"),
                    "data": row.get("data"),
                }
                for row in (step.get("tool_evidence") or [])[:12]
            ],
        })
    envelope["steps"] = compact_steps
    text = json.dumps(envelope, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    per_step = max(400, (max_chars - 400) // max(1, len(compact_steps)))
    shrunk: list[dict[str, Any]] = []
    for step in compact_steps:
        body = json.dumps(step, default=str, ensure_ascii=False)
        if len(body) > per_step:
            step = {
                **step,
                "summary": (step.get("summary") or "")[: min(600, per_step // 3)],
                "tool_evidence": (step.get("tool_evidence") or [])[:6],
            }
        shrunk.append(step)
    envelope["steps"] = shrunk
    text = json.dumps(envelope, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    rollup_only = json.dumps({"rollup": rollup, "steps": [
        {"step": s.get("step"), "summary": (s.get("summary") or "")[:400]}
        for s in shrunk
    ]}, default=str, ensure_ascii=False)
    if len(rollup_only) <= max_chars:
        return rollup_only
    return rollup_only[:max_chars]


def args_digest(args: dict[str, Any] | None) -> dict[str, Any]:
    """Stable, size-capped argument summary for traces (never full raw args)."""
    if not args:
        return {}
    out: dict[str, Any] = {}
    for key, value in list(args.items())[:10]:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        out[key] = text if len(text) <= 80 else text[:77] + "..."
    return out


def _digest_text(text: str) -> str:
    # MCP text blocks frequently contain JSON even when structured_content is
    # unavailable. Parse it first so useful issue keys/fields survive instead
    # of reducing the payload to generic head/tail line counts.
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        return build_digest(parsed)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= _HEAD_SAMPLE + _TAIL_SAMPLE:
        return text[:400]
    head = " | ".join(lines[:_HEAD_SAMPLE])
    tail = " | ".join(lines[-_TAIL_SAMPLE:])
    return f"{len(lines)} lines; head: {head[:200]} ... tail: {tail[:150]}"


def _digest_dict(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, list):
            sample = ", ".join(
                json.dumps(item, default=str)[:_NESTED_ITEM_SAMPLE_MAX]
                for item in value[:_HEAD_SAMPLE]
            )
            suffix = " ..." if len(value) > _HEAD_SAMPLE else ""
            parts.append(f"{key}: list[{len(value)}] [{sample}{suffix}]")
        elif isinstance(value, dict):
            parts.append(f"{key}: dict[{len(value)}]")
        elif isinstance(value, str) and len(value) > _STRING_SAMPLE_MAX:
            parts.append(f"{key}: {value[:_STRING_SAMPLE_MAX]}...")
        else:
            parts.append(f"{key}: {value}")
    return "; ".join(parts)[:600]


def _digest_list(items: list[Any]) -> str:
    if not items:
        return "empty list"
    sample = items[:_HEAD_SAMPLE]
    head = ", ".join(json.dumps(i, default=str)[:_STRING_SAMPLE_MAX] for i in sample)
    return f"list[{len(items)}]: [{head}{' ...' if len(items) > _HEAD_SAMPLE else ''}]"[:600]
