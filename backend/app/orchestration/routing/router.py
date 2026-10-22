"""Router — smalltalk fast path; everything else uses the live classifier."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.ai.prompts import ROUTER_SYSTEM
from app.ai.provider import LLMProvider
from app.core.llm_budgets import conversation_tail, router_conversation_chars
from app.models.plan import RouterOutput

logger = logging.getLogger(__name__)

_SMALLTALK_RE = re.compile(
    r"""
    ^\s*
    (?:
        (?:hi|hello|hey)(?:\s+there)?
        (?:[\s,.]+(?:what\s+can\s+you\s+do|who\s+are\s+you|help))?
        |
        (?:thanks|thank\s+you)
        |
        good\s+(?:morning|afternoon|evening)
        |
        (?:what\s+can\s+you\s+do|who\s+are\s+you)
        |
        help
    )
    [\s?.!]*
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TEMPORAL_RE = re.compile(
    r"\b(latest|current|next|upcoming|now|today|this week|this sprint|last sprint)\b",
    re.IGNORECASE,
)


def is_smalltalk(text: str) -> bool:
    return bool(_SMALLTALK_RE.match((text or "").strip()))


@dataclass
class RouterDecisionResult:
    intent: str
    path: str
    direct_answer: str | None
    temporal_sensitivity: bool
    reason: str
    llm_used: bool = False


async def classify(goal: str, provider: LLMProvider,
                   conversation: str = "") -> RouterDecisionResult:
    temporal = bool(_TEMPORAL_RE.search(goal))
    if is_smalltalk(goal):
        return RouterDecisionResult(
            intent="simple", path="fast", direct_answer=None,
            temporal_sensitivity=False, reason="greeting or meta only",
            llm_used=False)
    try:
        user = f"User message: {goal}"
        if conversation:
            user += f"\nConversation context: {conversation_tail(conversation, router_conversation_chars())}"
        user += "\nREQUEST_JSON: " + json.dumps(
            {"type": "router_request", "message": goal,
             "conversation": conversation_tail(conversation, router_conversation_chars())},
            default=str,
        )
        output, _usage = await provider.generate_structured(
            ROUTER_SYSTEM, user, RouterOutput, tier="cheap")
    except Exception as exc:  # noqa: BLE001
        logger.warning("router LLM failed (%s); defaulting to plan path", exc)
        return RouterDecisionResult(
            intent="complex", path="plan", direct_answer=None,
            temporal_sensitivity=temporal, reason="router error → plan path",
            llm_used=False)
    if output.intent == "simple" and output.direct_answer:
        return RouterDecisionResult(
            intent="simple", path="fast", direct_answer=output.direct_answer,
            temporal_sensitivity=output.temporal_sensitivity, reason="router LLM",
            llm_used=True)
    if output.intent == "clarify" and output.direct_answer:
        return RouterDecisionResult(
            intent="clarify", path="fast", direct_answer=output.direct_answer,
            temporal_sensitivity=output.temporal_sensitivity or temporal,
            reason="router LLM: clarify", llm_used=True)
    return RouterDecisionResult(
        intent=output.intent, path="plan", direct_answer=output.direct_answer,
        temporal_sensitivity=output.temporal_sensitivity or temporal,
        reason="router LLM", llm_used=True)
