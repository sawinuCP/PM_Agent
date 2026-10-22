"""Token accounting and per-agent prompt trimming (memory layer 1).

Section-aware assembly preserves role, task envelope, and upstream execution
context. Only optional L4 semantic hint blocks may shrink when over budget.
"""

from __future__ import annotations

import logging

from app.ai.prompts import WORKER_SYSTEM_ENVELOPE
from app.ai.tokens import count_tokens

logger = logging.getLogger(__name__)


def trim_to_budget(text: str, budget: int) -> str:
    """Legacy flat trim — not used for worker system prompts in the L1 policy."""
    if count_tokens(text) <= budget:
        return text
    keep = max(64, int(budget * 3.2))
    trimmed = text[:keep]
    logger.warning(
        "L1 flat trim applied: %d -> ~%d tokens (budget=%d)",
        count_tokens(text), count_tokens(trimmed), budget,
    )
    return trimmed + "\n...[trimmed to fit token budget]"


def _truncate_block(text: str, label: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    suffix = f"\n...[{label} trimmed to fit token budget]"
    keep = max(32, max_chars - len(suffix))
    return text[:keep] + suffix


def trim_system_prompt(
    budget: int,
    *,
    role: str,
    agent_name: str,
    step_id: str,
    task: str,
    protected_blocks: list[tuple[str, str]] | None = None,
    shrinkable_blocks: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble worker system prompt; shrink **semantic hints only** when over budget.

    Protected (never trimmed): YAML role, task envelope, upstream step context.
    Shrinkable: L4 semantic historical hints (optional soft facts).
    """
    protected = [
        (label, content.strip())
        for label, content in (protected_blocks or [])
        if content and str(content).strip()
    ]
    shrinkable: list[tuple[str, str]] = [
        (label, content.strip())
        for label, content in (shrinkable_blocks or [])
        if content and str(content).strip()
    ]
    active_shrinkable = [content for _, content in shrinkable]

    def render(*, shrinkable_parts: list[str], role_text: str = role) -> str:
        context_parts = [content for _, content in protected]
        context_parts.extend(part for part in shrinkable_parts if part)
        context_block = "\n".join(context_parts)
        envelope = WORKER_SYSTEM_ENVELOPE.format(
            agent_name=agent_name,
            step_id=step_id,
            task=task,
            context_block=context_block,
        )
        role_clean = (role_text or "").strip()
        if role_clean:
            return f"{role_clean}\n\n{envelope}"
        return envelope

    prompt = render(shrinkable_parts=active_shrinkable)
    if count_tokens(prompt) <= budget:
        return prompt

    original_tokens = count_tokens(prompt)
    trimmed_labels: list[str] = []

    for index, (label, _content) in enumerate(shrinkable):
        while active_shrinkable[index] and count_tokens(
            render(shrinkable_parts=active_shrinkable),
        ) > budget:
            trimmed_labels.append(label)
            new_len = max(0, int(len(active_shrinkable[index]) * 0.75))
            if new_len < 80:
                active_shrinkable[index] = ""
                break
            active_shrinkable[index] = _truncate_block(
                active_shrinkable[index], label, max_chars=new_len,
            )

        if count_tokens(render(shrinkable_parts=active_shrinkable)) <= budget:
            break

        if active_shrinkable[index]:
            trimmed_labels.append(f"{label}:dropped")
            active_shrinkable[index] = ""

    final = render(shrinkable_parts=active_shrinkable)
    if count_tokens(final) > budget:
        logger.warning(
            "L1 over budget after semantic trim agent=%s step=%s budget=%d "
            "tokens=%d — keeping full role/upstream/envelope",
            agent_name,
            step_id,
            budget,
            count_tokens(final),
        )
    elif trimmed_labels:
        logger.info(
            "L1 semantic trim agent=%s step=%s budget=%d "
            "tokens_before=%d tokens_after=%d adjusted=%s",
            agent_name,
            step_id,
            budget,
            original_tokens,
            count_tokens(final),
            sorted(set(trimmed_labels)),
        )
    return final
