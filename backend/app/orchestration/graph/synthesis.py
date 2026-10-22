"""Synthesis prompt composition for the multi-step aggregator."""

from __future__ import annotations

from app.ai.prompts import SYNTHESIS_SYSTEM
from app.core.llm_budgets import (
    conversation_tail,
    synthesis_conversation_chars,
    synthesis_evidence_chars,
)
from app.mcp_integration.digest import build_synthesis_evidence, pack_synthesis_evidence_json


def compose_synthesis_prompt(
    *,
    goal: str,
    conversation: str,
    step_outputs: dict[str, str],
    entity_gaps: list[str] | None = None,
) -> tuple[str, str]:
    """Build (system, user) messages for the synthesis LLM call."""
    evidence = build_synthesis_evidence(step_outputs)
    evidence_json = pack_synthesis_evidence_json(
        step_outputs, max_chars=synthesis_evidence_chars())
    session_block = conversation_tail(
        (conversation or "").strip(),
        synthesis_conversation_chars(),
    )
    gap_block = ""
    if entity_gaps:
        gap_block = (
            f"\n\nEVIDENCE_GAPS (projects named in the goal but missing health "
            f"tool evidence — state this explicitly; do not invent numbers): "
            f"{entity_gaps}\n"
        )
    user = (
        f"User question: {goal}\n\n"
        f"Session (context only — do not quote prior numbers as facts):\n"
        f"{session_block}\n\n"
        f"EVIDENCE_JSON: {evidence_json}"
        f"{gap_block}"
    )
    _ = evidence  # kept for tests / future structured callers
    return SYNTHESIS_SYSTEM, user
