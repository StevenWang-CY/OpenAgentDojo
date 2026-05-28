"""Generator failure: fallback returned (not persisted); without fallback re-raises.

The contract from P1_DESIGN §0.4.5:

* a fallback string is returned with ``from_fallback=True``;
* the fallback is NEVER written to ``llm_cache`` — so a future retry
  can still heal the cache;
* without a fallback the exception propagates so the caller can
  decide what to do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.llm.cache import GeneratedOutput, get_or_generate
from app.models.llm_cache import LLMCache


@pytest.mark.asyncio
async def test_generator_error_returns_fallback_and_does_not_persist(
    db_session,
) -> None:
    async def _failing_generator() -> GeneratedOutput:
        raise RuntimeError("LLM provider not configured")

    result = await get_or_generate(
        db_session,
        domain="recommendation_diagnosis",
        content_hash="c" * 64,
        prompt_version=1,
        model_id="claude-haiku-4-5",
        generator=_failing_generator,
        fallback="deterministic fallback",
    )

    assert result.output == "deterministic fallback"
    assert result.cache_hit is False
    assert result.from_fallback is True
    assert result.model_id == "claude-haiku-4-5"

    # No row persisted — the fallback must not poison the cache.
    stmt = select(LLMCache).where(
        LLMCache.domain == "recommendation_diagnosis",
        LLMCache.content_hash == "c" * 64,
    )
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_generator_error_without_fallback_reraises(db_session) -> None:
    async def _failing_generator() -> GeneratedOutput:
        raise RuntimeError("LLM provider not configured")

    with pytest.raises(RuntimeError, match="LLM provider not configured"):
        await get_or_generate(
            db_session,
            domain="recommendation_diagnosis",
            content_hash="d" * 64,
            prompt_version=1,
            model_id="claude-haiku-4-5",
            generator=_failing_generator,
            fallback=None,
        )

    # And still no row landed.
    stmt = select(LLMCache).where(
        LLMCache.domain == "recommendation_diagnosis",
        LLMCache.content_hash == "d" * 64,
    )
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is None
