"""LLM cache miss writes a row, then a follow-up call hits the cache.

This is the round-trip determinism check: cold call invokes the
generator and persists a row; second call with the same key returns
the persisted bytes without calling the generator again.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.llm.cache import GeneratedOutput, get_or_generate
from app.models.llm_cache import LLMCache


@pytest.mark.asyncio
async def test_cache_miss_persists_then_second_call_hits(db_session) -> None:
    calls = {"count": 0}

    async def _generator() -> GeneratedOutput:
        calls["count"] += 1
        return GeneratedOutput(
            output="freshly-generated prose",
            input_tokens=42,
            output_tokens=11,
        )

    # First call — cold cache.
    first = await get_or_generate(
        db_session,
        domain="recommendation_why",
        content_hash="b" * 64,
        prompt_version=1,
        model_id="claude-haiku-4-5",
        generator=_generator,
    )
    assert first.cache_hit is False
    assert first.from_fallback is False
    assert first.output == "freshly-generated prose"
    assert first.model_id == "claude-haiku-4-5"
    assert calls["count"] == 1

    # Row landed.
    stmt = select(LLMCache).where(
        LLMCache.domain == "recommendation_why",
        LLMCache.content_hash == "b" * 64,
        LLMCache.prompt_version == 1,
    )
    persisted = (await db_session.execute(stmt)).scalar_one()
    assert persisted.output == "freshly-generated prose"
    assert persisted.input_tokens == 42
    assert persisted.output_tokens == 11

    # Second call — should hit the cache and skip the generator.
    second = await get_or_generate(
        db_session,
        domain="recommendation_why",
        content_hash="b" * 64,
        prompt_version=1,
        model_id="claude-haiku-4-5",
        generator=_generator,
    )
    assert second.cache_hit is True
    assert second.output == "freshly-generated prose"
    assert calls["count"] == 1  # generator NOT invoked again
