"""LLM-polish layer for recommendation diagnosis + per-mission "why" copy.

Wraps :mod:`app.recommendations.copy` with a read-through ``llm_cache``
following the chokepoint contract documented at P1_DESIGN §0.4. Two public
async helpers — :func:`generate_diagnosis` and :func:`generate_why` —
return polished prose on cache hit, generate-and-persist on miss, or
fall back to the deterministic templates from ``copy.py`` on any LLM
failure (and on the ``feature_llm_recommendation_prose`` kill switch).

Design choices:

* Hard cap of ``max_tokens=256`` per call (P1_DESIGN §0.4.7). The prose
  is a single sentence; nothing legitimate runs longer.
* The cache key tuple is
  ``(weakest_dim, weakest_dim_avg → 1dp, recommended_mission_ids, rubric_version, prompt_version)``
  for diagnosis and
  ``(mission_id, weakest_dim, failure_mode)`` for per-mission "why".
  Both go through :func:`app.llm.canonical_content_hash`.
* The fallback string is exactly the deterministic copy from
  :mod:`app.recommendations.copy` — so a Bedrock 5xx degrades to the
  pre-LLM behaviour without surfacing a partial / empty card.
* The feature flag ``settings.feature_llm_recommendation_prose`` defaults
  to ``False`` via ``getattr`` (Agent C owns the actual settings field).
  When False we short-circuit and never reach the chokepoint.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import (
    PROMPT_VERSION,
    GeneratedOutput,
    canonical_content_hash,
    get_or_generate,
    render_prompt,
)
from app.llm.client import build_anthropic_client
from app.recommendations.copy import (
    WhyMode,
    diagnosis_for,
    why_for_mission,
)

# The recommendation prose is a one-sentence answer; cap aggressively so
# a model run-on can't dominate the daily token budget rule (P1_DESIGN §0.4.7).
_MAX_TOKENS = 256

# Logical model id — resolved at call time by the AnthropicClient via
# :func:`app.llm.client.resolve_model_id`. Pinned to Haiku for the
# recommendation surface: the prose is short and the per-call latency
# budget is tight (the surface is loaded inline with /auth/me and
# /me/recommendations).
_MODEL_ID = "claude-haiku-4-5"

_DIAGNOSIS_DOMAIN = "recommendation_diagnosis"
_WHY_DOMAIN = "recommendation_why"

# Rubric version literal embedded in the diagnosis cache key. Mirrors the
# string used in :mod:`app.llm.domains` per-domain rules — bumping the
# rubric must invalidate the prose alongside the underlying scoring.
_RUBRIC_VERSION_LITERAL = "v1"

# One-line plain-English description per rubric dimension. Mirrors the
# system-prompt list so the model gets a consistent anchor when the
# template is rendered. Authored to match the diagnosis prompt
# (``apps/api/app/llm/prompts/recommendation_diagnosis.md``).
_DIMENSION_SUMMARY: dict[str, str] = {
    "final_correctness": ("the merged patch actually fixes the bug and does not regress."),
    "verification": ("they ran the test that proves the fix before submitting."),
    "agent_review": ("they read the agent's diff line-by-line, not just the prose summary."),
    "prompt_quality": (
        "their prompts name specific files / symbols and a checkable success condition."
    ),
    "context_selection": ("they opened the files the bug actually lives in before prompting."),
    "safety": ("they refused or pushed back on unsafe destructive commands."),
    "diff_minimality": ("they kept the agent's patch focused on the bug, not a refactor spree."),
}


def _llm_enabled() -> bool:
    """Return True when the LLM prose layer is permitted.

    Defaults to False via ``getattr`` — Agent C owns adding the
    ``feature_llm_recommendation_prose`` field to Settings. Until it
    lands the surface stays on the deterministic fallback and no
    Bedrock call escapes the process.
    """
    settings = get_settings()
    return bool(getattr(settings, "feature_llm_recommendation_prose", False))


def _build_client() -> Any:
    """Construct the AnthropicClient used for prose generation.

    Mirrors :func:`app.reports.coaching._build_client` so test sites can
    monkeypatch the same import path to inject a fake SDK.
    """
    return build_anthropic_client(_MODEL_ID)


def _extract_text(resp: Any) -> str:
    """Pull the first text block from an Anthropic SDK response.

    Defensive — both the SDK and the test-harness mocks shape responses
    as ``resp.content[0].text``; older mocks may surface a plain string.
    """
    content = getattr(resp, "content", None)
    if isinstance(content, list) and content:
        head = content[0]
        text = getattr(head, "text", None)
        if isinstance(text, str):
            return text
        if isinstance(head, dict):
            dict_text = head.get("text")
            if isinstance(dict_text, str):
                return dict_text
    if isinstance(content, str):
        return content
    raise RuntimeError("recommendation prose: unexpected LLM response shape")


def _resolve_avg(
    *,
    weakest_dim: str | None,
    user_history: Any | None,
) -> float:
    """Compute the rounded-to-1dp average score on ``weakest_dim``.

    Returns ``0.0`` when no signal is available (cold-start). The
    rounding rule mirrors the per-domain canonicalisation documented at
    :mod:`app.llm.domains` so a 0.05 floating-point jitter doesn't bust
    the cache key.
    """
    if weakest_dim is None or user_history is None:
        return 0.0
    best_attempts = getattr(user_history, "best_attempts", None)
    if not best_attempts:
        return 0.0
    scores: list[float] = []
    for attempt in best_attempts.values():
        dims = getattr(attempt, "dimensions", None) or {}
        raw = dims.get(weakest_dim)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and raw >= 0:
            scores.append(float(raw))
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _normalised_mission_ids(mission_ids: Iterable[str]) -> list[str]:
    """Order-preserving list shape for the diagnosis cache key.

    ``canonical_content_hash`` serialises tuples and lists identically.
    Keep it a list at the call boundary; the order IS part of the
    determinism contract (the engine's ranking is stable, so re-ordering
    here would silently mutate the cache key).
    """
    return [str(m) for m in mission_ids]


async def generate_diagnosis(
    db: AsyncSession,
    *,
    weakest_dim: str | None,
    weakest_dim_avg: float | None = None,
    recommended_mission_ids: Iterable[str] = (),
    weakest_dim_attempts: int = 0,
    user_history: Any | None = None,
) -> str:
    """Return polished diagnosis prose (or the deterministic fallback).

    Routes through :func:`app.llm.cache.get_or_generate`; cache hits cost
    one SELECT and return the persisted bytes verbatim. On a miss we call
    the Anthropic client with the rendered ``recommendation_diagnosis``
    prompt and persist the canonical bytes. On any failure (LLM down,
    fallback flag off, civitas missing) the deterministic copy from
    :func:`app.recommendations.copy.diagnosis_for` is returned unchanged.

    ``weakest_dim_avg`` may be precomputed by the caller or, when ``None``
    + ``user_history`` is provided, derived locally. Either way it is
    rounded to 1 decimal place before entering the cache key.
    """
    fallback = diagnosis_for(weakest_dim)

    # Cold-start / all-graded surfaces don't carry a weakest dim — the
    # deterministic copy already addresses those cases and there is no
    # signal for the model to riff on. Short-circuit.
    if weakest_dim is None:
        return fallback

    if not _llm_enabled():
        return fallback

    if weakest_dim_avg is None:
        weakest_dim_avg = _resolve_avg(weakest_dim=weakest_dim, user_history=user_history)
    avg_rounded = round(float(weakest_dim_avg), 1)
    ids_list = _normalised_mission_ids(recommended_mission_ids)

    cache_inputs = {
        "weakest_dim": weakest_dim,
        "weakest_dim_avg": avg_rounded,
        "recommended_mission_ids": ids_list,
        "rubric_version": _RUBRIC_VERSION_LITERAL,
    }
    content_hash = canonical_content_hash(cache_inputs)

    async def _generator() -> GeneratedOutput:
        client = _build_client()
        system, user_prompt = render_prompt(
            "recommendation_diagnosis",
            weakest_dim=weakest_dim,
            weakest_dim_avg=avg_rounded,
            weakest_dim_attempts=int(weakest_dim_attempts),
            rubric_version=_RUBRIC_VERSION_LITERAL,
        )
        resp = await client.messages_create(
            model=_MODEL_ID,
            max_tokens=_MAX_TOKENS,
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp)
        usage = getattr(resp, "usage", None)
        return GeneratedOutput(
            output=text.strip() or fallback,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    try:
        cached = await get_or_generate(
            db,
            domain=_DIAGNOSIS_DOMAIN,
            content_hash=content_hash,
            prompt_version=PROMPT_VERSION,
            model_id=_MODEL_ID,
            generator=_generator,
            fallback=fallback,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "recommendation prose: get_or_generate raised for diagnosis "
            "(weakest_dim={}, ids={}): {}",
            weakest_dim,
            ids_list,
            exc,
        )
        return fallback
    return cached.output or fallback


async def generate_why(
    db: AsyncSession,
    *,
    mission_id: str,
    weakest_dim: str | None,
    failure_mode: str | None = None,
    expected_weak_dim: str | None = None,
    alignment: float = 0.0,
    freshness_fresh: bool = False,
    mode: WhyMode = "normal",
) -> str:
    """Return polished per-mission "why" prose (or the deterministic fallback).

    Identical chokepoint discipline to :func:`generate_diagnosis`. The
    cache key is content-addressed on
    ``(mission_id, weakest_dim, failure_mode)`` per design §P1-2; the
    rendered prompt also carries the alignment score so the model can
    reason about the strength of the dimension link.

    Falls back to :func:`app.recommendations.copy.why_for_mission` on
    any failure path — that string is what shipped before the LLM
    polish layer, so a degraded experience here matches pre-LLM
    behaviour exactly.
    """
    fallback = why_for_mission(
        mission_id=mission_id,
        expected_weak_dim=expected_weak_dim,
        weakest_dim=weakest_dim,
        alignment=alignment,
        freshness_fresh=freshness_fresh,
        mode=mode,
    )
    # No signal to ask the model about — cold-start + all-graded modes
    # already have hand-crafted deterministic copy.
    if weakest_dim is None or mode == "all_graded":
        return fallback
    if not _llm_enabled():
        return fallback

    failure_mode_value = (failure_mode or "").strip()
    cache_inputs = {
        "mission_id": str(mission_id),
        "weakest_dim": weakest_dim,
        "failure_mode": failure_mode_value,
    }
    content_hash = canonical_content_hash(cache_inputs)

    async def _generator() -> GeneratedOutput:
        client = _build_client()
        system, user_prompt = render_prompt(
            "recommendation_why",
            mission_id=mission_id,
            failure_mode_title=failure_mode_value or "(unspecified)",
            weakest_dim=weakest_dim,
            alignment=round(float(alignment), 2),
            dimension_summary=_DIMENSION_SUMMARY.get(
                weakest_dim, "the dimension this mission is queued against."
            ),
        )
        resp = await client.messages_create(
            model=_MODEL_ID,
            max_tokens=_MAX_TOKENS,
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp)
        usage = getattr(resp, "usage", None)
        return GeneratedOutput(
            output=text.strip() or fallback,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    try:
        cached = await get_or_generate(
            db,
            domain=_WHY_DOMAIN,
            content_hash=content_hash,
            prompt_version=PROMPT_VERSION,
            model_id=_MODEL_ID,
            generator=_generator,
            fallback=fallback,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "recommendation prose: get_or_generate raised for why "
            "(mission_id={}, weakest_dim={}): {}",
            mission_id,
            weakest_dim,
            exc,
        )
        return fallback
    return cached.output or fallback


__all__ = [
    "generate_diagnosis",
    "generate_why",
]
