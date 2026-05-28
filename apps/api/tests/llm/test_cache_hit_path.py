"""LLM cache hit path: seed a row, call get_or_generate, generator MUST NOT run.

This is the determinism contract — once a row is in the cache, every
subsequent call for the same (domain, content_hash, prompt_version)
returns the cached bytes and never touches the LLM.
"""

from __future__ import annotations

import pytest

from app.llm.cache import GeneratedOutput, get_or_generate
from app.models.llm_cache import LLMCache


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_bytes_without_calling_generator(
    db_session,
) -> None:
    row = LLMCache(
        domain="recommendation_diagnosis",
        content_hash="a" * 64,
        prompt_version=1,
        model_id="claude-haiku-4-5",
        output="cached prose",
        input_tokens=10,
        output_tokens=20,
    )
    db_session.add(row)
    await db_session.commit()

    calls = {"count": 0}

    async def _generator() -> GeneratedOutput:
        calls["count"] += 1
        raise AssertionError("generator must not be called on cache hit")

    result = await get_or_generate(
        db_session,
        domain="recommendation_diagnosis",
        content_hash="a" * 64,
        prompt_version=1,
        model_id="claude-haiku-4-5",
        generator=_generator,
        fallback="fallback should not be returned either",
    )

    assert result.output == "cached prose"
    assert result.cache_hit is True
    assert result.from_fallback is False
    assert result.model_id == "claude-haiku-4-5"
    assert calls["count"] == 0
