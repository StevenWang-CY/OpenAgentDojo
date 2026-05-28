"""Prose layer falls back to deterministic copy on generator failure.

When the Anthropic client raises (Bedrock down, no token, civitas
missing) the prose helper MUST return the deterministic copy from
:mod:`app.recommendations.copy`, increment
``llm_generation_failed_total``, and refuse to persist a row in
``llm_cache`` — otherwise a future retry could never heal the cache.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.llm_cache import LLMCache
from app.recommendations import prose
from app.recommendations.copy import diagnosis_for, why_for_mission


class _BoomClient:
    """Stand-in AnthropicClient whose ``messages_create`` always raises."""

    async def messages_create(self, **_: object) -> object:
        raise RuntimeError("simulated bedrock outage")


@pytest.fixture
def _force_llm_enabled(monkeypatch):
    """Force ``feature_llm_recommendation_prose`` ON for the test.

    The Settings field is Agent C's slice and may default to False at
    runtime; the prose layer reads it via ``getattr``. Patching the
    helper directly avoids depending on the live Settings shape.
    """
    monkeypatch.setattr(prose, "_llm_enabled", lambda: True)


@pytest.fixture
def _patch_boom_client(monkeypatch):
    monkeypatch.setattr(prose, "_build_client", lambda: _BoomClient())


@pytest.mark.asyncio
async def test_generate_diagnosis_falls_back_when_generator_raises(
    db_session, _force_llm_enabled, _patch_boom_client
) -> None:
    output = await prose.generate_diagnosis(
        db_session,
        weakest_dim="agent_review",
        weakest_dim_avg=2.1,
        recommended_mission_ids=("alpha", "beta"),
        weakest_dim_attempts=3,
    )

    # Falls back to the deterministic copy exactly.
    assert output == diagnosis_for("agent_review")

    # No row landed — a fallback must never poison the cache.
    rows = (
        await db_session.execute(
            select(LLMCache).where(LLMCache.domain == "recommendation_diagnosis")
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_generate_why_falls_back_when_generator_raises(
    db_session, _force_llm_enabled, _patch_boom_client
) -> None:
    output = await prose.generate_why(
        db_session,
        mission_id="goroutine-leak",
        weakest_dim="verification",
        failure_mode="goroutine_leak",
        expected_weak_dim="verification",
        alignment=1.0,
        freshness_fresh=False,
        mode="normal",
    )

    expected = why_for_mission(
        mission_id="goroutine-leak",
        expected_weak_dim="verification",
        weakest_dim="verification",
        alignment=1.0,
        freshness_fresh=False,
        mode="normal",
    )
    assert output == expected

    rows = (
        await db_session.execute(
            select(LLMCache).where(LLMCache.domain == "recommendation_why")
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_llm_disabled_short_circuits_without_calling_client(
    db_session, monkeypatch
) -> None:
    """When the feature flag is OFF the prose layer never touches the LLM."""

    monkeypatch.setattr(prose, "_llm_enabled", lambda: False)

    def _explode() -> None:
        raise AssertionError("client must not be constructed when LLM is disabled")

    monkeypatch.setattr(prose, "_build_client", _explode)

    output = await prose.generate_diagnosis(
        db_session,
        weakest_dim="diff_minimality",
        weakest_dim_avg=1.5,
        recommended_mission_ids=("x",),
    )
    assert output == diagnosis_for("diff_minimality")

    # Settings call still works without monkeypatching it — confirm a
    # call doesn't crash on the live Settings shape (defensive belt).
    assert get_settings() is not None
