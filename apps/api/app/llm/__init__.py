"""LLM substrate (P1 §0.4).

This package is the single chokepoint every LLM-augmented surface in
the codebase goes through. The invariants it preserves:

* one canonical output per ``(domain, content_hash, prompt_version)``;
* the determinism property downstream signatures depend on (the bytes
  hashed into a verify envelope or replay artefact are the cached
  bytes, never a fresh model call);
* a uniform fallback boundary so the product still works when the LLM
  is unavailable;
* a uniform observability boundary so a single set of dashboards covers
  every LLM use site.

Usage
-----

.. code-block:: python

    from app.llm import (
        GeneratedOutput,
        canonical_content_hash,
        get_or_generate,
        get_prompt_version,
        render_prompt,
    )
    from app.llm.client import build_anthropic_client

    payload = {
        "weakest_dim": "agent_review",
        "weakest_dim_avg": round(user_avg, 1),
        "recommended_mission_ids": tuple(mission_ids),
        "rubric_version": "v1",
    }
    content_hash = canonical_content_hash(payload)
    system, user = render_prompt("recommendation_diagnosis", **payload)
    client = build_anthropic_client("claude-haiku-4-5")

    async def _generate() -> GeneratedOutput:
        resp = await client.messages_create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text
        usage = getattr(resp, "usage", None)
        return GeneratedOutput(
            output=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    result = await get_or_generate(
        db,
        domain="recommendation_diagnosis",
        content_hash=content_hash,
        prompt_version=get_prompt_version(),
        model_id="claude-haiku-4-5",
        generator=_generate,
        fallback="You skip the diff most of the time — try these three.",
    )
    # ``result.output`` is the user-facing prose. ``result.cache_hit``
    # tells you whether it came from llm_cache. ``result.from_fallback``
    # tells you whether the LLM was unavailable.
"""

from __future__ import annotations

from app.llm.cache import (
    CachedOutput,
    GeneratedOutput,
    GeneratorCallable,
    get_or_generate,
)
from app.llm.client import build_anthropic_client, resolve_model_id
from app.llm.domains import (
    ALLOWED_DOMAINS,
    PROMPT_VERSION,
    LLMDomain,
    get_prompt_version,
    is_known_domain,
)
from app.llm.hashing import canonical_content_hash
from app.llm.prompt_loader import render_prompt

__all__ = [
    "ALLOWED_DOMAINS",
    "PROMPT_VERSION",
    "CachedOutput",
    "GeneratedOutput",
    "GeneratorCallable",
    "LLMDomain",
    "build_anthropic_client",
    "canonical_content_hash",
    "get_or_generate",
    "get_prompt_version",
    "is_known_domain",
    "render_prompt",
    "resolve_model_id",
]
