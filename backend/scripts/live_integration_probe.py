"""Cost-capped live check for CommandCode GLM and Voyage embeddings.

Makes one 16-token structured-output LLM call and two short embedding calls.
It never prints credentials and is intentionally a manual, one-shot probe.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# Token/timeout caps only — LLM and embeddings providers come from backend/.env.
os.environ["LLM_MAX_TOKENS"] = "16"
os.environ["LLM_TIMEOUT_S"] = "20"


async def main() -> int:
    from app.ai.provider import get_provider
    from app.core.settings import get_settings
    from app.memory.embeddings import cosine, embed_text
    from app.models.schemas import RouterOutput

    get_settings.cache_clear()
    settings = get_settings()
    from app.ai.provider import resolve_llm_provider

    name = resolve_llm_provider(settings)
    print(
        f"provider={name} (backend/.env LLM_PROVIDER={settings.llm_provider}) "
        f"llm={settings.openai_model} embeddings={settings.voyage_model} "
        f"dimension={settings.embeddings_dim} (keys not shown)"
    )
    if name != "openai":
        print("LIVE INTEGRATION SKIPPED: set LLM_PROVIDER=openai in backend/.env.")
        return 0
    failures: list[str] = []
    routed = None
    usage = None
    query: list[float] = []
    similarity = 0.0

    try:
        routed, usage = await get_provider().generate_structured(
            'Reply ONLY with JSON: {"intent":"complex","direct_answer":null,'
            '"temporal_sensitivity":false}',
            "Forecast our next sprint velocity.",
            RouterOutput,
            tier="cheap",
        )
    except Exception as exc:  # noqa: BLE001 - bounded probe reports safely
        cause = exc.__cause__
        cause_name = type(cause).__name__ if cause else "unknown"
        status = getattr(cause, "status_code", None)
        failures.append(f"GLM: {type(exc).__name__} caused by {cause_name}"
                        + (f" (HTTP {status})" if status else ""))

    try:
        sample = "A delivery team completed 42 points in its latest sprint."
        query, document = await asyncio.gather(
            asyncio.to_thread(embed_text, sample, input_type="query"),
            asyncio.to_thread(embed_text, sample, input_type="document"),
        )
        similarity = cosine(query, document)
        if len(query) != settings.embeddings_dim or query == document:
            raise RuntimeError("Voyage returned invalid retrieval embeddings")
    except Exception as exc:  # noqa: BLE001 - bounded probe reports safely
        cause = exc.__cause__
        failures.append(
            f"Voyage: {type(exc).__name__}"
            + (f" caused by {type(cause).__name__}" if cause else "")
        )

    if failures:
        if query:
            print(
                f"LIVE VOYAGE OK: dimension={len(query)} "
                f"retrieval_cosine={similarity:.4f}"
            )
        print("LIVE INTEGRATION FAILED: " + "; ".join(failures))
        return 1

    assert routed is not None and usage is not None
    print(
        f"LIVE INTEGRATION OK: intent={routed.intent} "
        f"tokens={usage.prompt_tokens}->{usage.completion_tokens} "
        f"voyage_dim={len(query)} retrieval_cosine={similarity:.4f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
