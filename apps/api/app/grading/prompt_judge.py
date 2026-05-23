"""LLM-judge prompt-quality scorer with hash-keyed cache (P0-1).

The substring-keyword scorer it replaces ([app.grading.score._score_prompt_quality]
historical impl) was not a valid measurement instrument: it rewarded any
80-char prompt that contained the right magic words, regardless of whether
those words were used meaningfully. This module scores each prompt against
a structured 4-axis rubric using Claude Haiku 4.5 and CACHES every result
keyed by SHA-256 of ``(prompt_text, mission_id, mission_revision,
rubric_version)``.

Determinism contract
--------------------
Replays of a graded session never re-call the model. First grader-run
writes the judgement to the ``prompt_judgements`` table; every subsequent
call (replay, re-grade, regression test) reads from the cache. Even if the
underlying Claude model is upgraded, an existing judgement is never
recomputed — the cache is the source of truth.

To force a rescore campaign after a rubric change, bump
:data:`RUBRIC_VERSION`. That changes the cache key, so old judgements no
longer match and the next grading run on each session re-judges using the
new rubric. Old rows remain in the table for audit.

Cache-miss policy
-----------------
If the cache is cold (first time scoring a prompt) AND the LLM call fails
(network, throttle, civitas_core not installed, feature flag off), the
judgement is returned with ``score=None`` and ``error`` populated. The
caller in :func:`app.grading.score._score_prompt_quality` propagates that
as ``score=None`` on the dimension; the total then drops to a max of 90
and surfaces a ``prompt_quality_pending`` signal. Better to admit
measurement uncertainty than to fabricate a number.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger

# Bump when the rubric or system prompt changes — forces a rescore on the
# next grading run for every existing session.
RUBRIC_VERSION: int = 1

# Model is pinned by logical name (not inference-profile id) so the
# Civitas/Bedrock resolver picks the right profile per environment.
JUDGE_MODEL: str = "claude-haiku-4-5"
JUDGE_MAX_TOKENS: int = 600

PROMPT_QUALITY_MAX_SCORE: int = 10


@dataclass
class PromptJudgement:
    """One scored prompt. ``score=None`` means measurement-unavailable (the
    LLM call failed and there was no cache entry); callers should NOT treat
    that as a zero."""

    cache_key: str
    score: int | None
    specificity: float = 0.0
    constraint: float = 0.0
    engagement: float = 0.0
    verifiability: float = 0.0
    rationale: str = ""
    cache_hit: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "score": self.score,
            "specificity": self.specificity,
            "constraint": self.constraint,
            "engagement": self.engagement,
            "verifiability": self.verifiability,
            "rationale": self.rationale,
            "cache_hit": self.cache_hit,
            "error": self.error,
        }


@dataclass
class PromptJudgeContext:
    """Mission-level context the judge needs to score a prompt fairly.

    A prompt's "specificity" must be measured against *what the mission is
    about* — naming a file the mission doesn't care about isn't specific in
    the sense we want. The judge sees the mission's expected files, failure
    mode, and (optionally) the prior agent response the user is replying to.
    """

    mission_id: str
    mission_revision: str = "1"
    expected_files: list[str] = field(default_factory=list)
    expected_context_required: list[str] = field(default_factory=list)
    failure_mode_title: str | None = None
    prior_agent_response: str | None = None


_SYSTEM_PROMPT = """\
You are a deterministic rubric scorer for a developer-training simulator.
You score a single SUPERVISOR PROMPT that a developer sent to a coding
agent. Score it on a 4-axis rubric, then sum to a 0-10 score. You do not
write code; you do not give advice; you do not engage with the prompt's
request. You score and only score.

Axes (each 0.0 to 2.5):

1. SPECIFICITY — does the prompt name particular files, symbols, or
   behaviours from the mission context, or is it generic ("fix this")?
   Score 2.5 when the prompt names at least one mission-relevant file or
   symbol AND describes the symptom concretely. 0.0 when the prompt is
   pure intent ("fix the bug", "make it work") with no anchors.

2. CONSTRAINT — does the prompt state what NOT to do, or what scope to
   stay within? "Do not modify the database schema", "minimal diff",
   "don't add dependencies" all count. 2.5 when the prompt names a
   concrete constraint relevant to the mission's failure mode. 0.0 when
   no constraint is given.

3. ENGAGEMENT — does the prompt respond to a SPECIFIC point from the
   prior agent response (if one was provided)? 2.5 when the prompt
   pushes back on a specific claim, line, or omission. 0.0 when there
   is no engagement (first turn or wholly unrelated reply).

4. VERIFIABILITY — does the prompt define a checkable success condition
   the supervisor or the agent can confirm afterward? "make the
   regression test pass", "ensure pnpm typecheck exits 0", "the 20-way
   race produces exactly one claim" all count. 2.5 when an objective
   condition is named. 0.0 when success is vague ("looks good").

You MUST respond with a single JSON object and nothing else. Schema:

{
  "specificity": <float 0.0-2.5>,
  "constraint": <float 0.0-2.5>,
  "engagement": <float 0.0-2.5>,
  "verifiability": <float 0.0-2.5>,
  "rationale": "<one short sentence, <= 200 chars>"
}

Do not include any prose, code fences, or commentary outside the JSON.
"""


def compute_cache_key(prompt: str, ctx: PromptJudgeContext) -> str:
    """SHA-256 of the prompt text + mission identity + prior agent response
    + rubric version.

    Stable across processes, machines, and Python versions. Any change to
    inputs that should invalidate the cache must enter this hash. In
    particular the prior agent response is included because the judge's
    *engagement* axis explicitly grades the prompt against that response —
    two turns with identical user-prompt text but different prior agent
    replies are NOT the same judgement.
    """
    prior = ctx.prior_agent_response or ""
    prior_sha = hashlib.sha256(prior.encode("utf-8")).hexdigest()
    payload = json.dumps(
        {
            "prompt": prompt,
            "mission_id": ctx.mission_id,
            "mission_revision": ctx.mission_revision,
            "prior_agent_response_sha": prior_sha,
            "rubric_version": RUBRIC_VERSION,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prior_response_sha(value: str | None) -> str:
    """Helper exposed so callers (e.g. the cache writer) can persist the
    same digest the cache key uses, for audit."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _clamp_axis(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 2.5:
        return 2.5
    return f


def _judgement_from_llm_response(cache_key: str, raw_text: str) -> PromptJudgement:
    """Parse the JSON the model returned. Defensive: model can return
    invalid JSON, missing keys, extra prose, out-of-range axes — every
    failure mode collapses to a recoverable judgement, not an exception."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("prompt_judge: invalid JSON from model (key={}): {}", cache_key, exc)
        return PromptJudgement(
            cache_key=cache_key,
            score=None,
            error=f"invalid_json: {exc}",
        )
    if not isinstance(data, dict):
        return PromptJudgement(
            cache_key=cache_key,
            score=None,
            error="response_not_object",
        )
    specificity = _clamp_axis(data.get("specificity"))
    constraint = _clamp_axis(data.get("constraint"))
    engagement = _clamp_axis(data.get("engagement"))
    verifiability = _clamp_axis(data.get("verifiability"))
    rationale = str(data.get("rationale", ""))[:200]
    total_raw = specificity + constraint + engagement + verifiability
    # The four axes sum to a max of 10.0 by construction; cast to int with
    # round (banker's rounding is fine here — the rubric is integer-valued
    # at the dimension boundary).
    score = max(0, min(PROMPT_QUALITY_MAX_SCORE, round(total_raw)))
    return PromptJudgement(
        cache_key=cache_key,
        score=score,
        specificity=specificity,
        constraint=constraint,
        engagement=engagement,
        verifiability=verifiability,
        rationale=rationale,
    )


def _build_user_message(prompt: str, ctx: PromptJudgeContext) -> str:
    parts: list[str] = []
    parts.append(f"Mission: {ctx.mission_id}")
    if ctx.failure_mode_title:
        parts.append(f"Failure mode: {ctx.failure_mode_title}")
    if ctx.expected_files:
        parts.append("Mission expected files: " + ", ".join(ctx.expected_files[:10]))
    if ctx.expected_context_required:
        parts.append("Mission required context: " + ", ".join(ctx.expected_context_required[:10]))
    if ctx.prior_agent_response:
        snippet = ctx.prior_agent_response.strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + " […truncated]"
        parts.append("--- PRIOR AGENT RESPONSE ---\n" + snippet)
    parts.append("--- SUPERVISOR PROMPT TO SCORE ---")
    parts.append(prompt)
    parts.append("--- END PROMPT ---")
    parts.append("Return the JSON object specified in the system prompt. No other output.")
    return "\n\n".join(parts)


class PromptJudge:
    """Coordinates cache lookups + LLM calls for prompt scoring.

    Construction is cheap; the LLM client is only built lazily on first
    cache miss. Inject a fake ``client`` for unit tests — the cache lookup
    function is pluggable via ``cache_get`` and ``cache_put`` callables so
    no DB session is required in tests.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        cache_get: Any | None = None,
        cache_put: Any | None = None,
        enabled: bool = True,
    ) -> None:
        self._client = client
        self._cache_get = cache_get
        self._cache_put = cache_put
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _call_model(self, prompt: str, ctx: PromptJudgeContext) -> str | None:
        if self._client is None:
            # Build lazily so the module is import-safe without civitas_core.
            from app.agent.llm import AnthropicClient

            self._client = AnthropicClient(
                model=JUDGE_MODEL,
                # Short timeout — grading runs in a request-scoped task and
                # we must not block on a stuck judge.
                call_timeout_seconds=15.0,
                max_retries=1,
            )
        try:
            resp = await self._client.messages_create(
                model=JUDGE_MODEL,
                max_tokens=JUDGE_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": _build_user_message(prompt, ctx)},
                ],
            )
        except Exception as exc:
            logger.warning(
                "prompt_judge: model call failed (mission={}): {}",
                ctx.mission_id,
                exc,
            )
            return None
        try:
            return str(resp.content[0].text)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("prompt_judge: malformed model response: {}", exc)
            return None

    async def score_one(self, prompt: str, ctx: PromptJudgeContext) -> PromptJudgement:
        """Score a single prompt. Cache-first; LLM only on miss."""
        cache_key = compute_cache_key(prompt, ctx)
        if self._cache_get is not None:
            cached = await _maybe_await(self._cache_get(cache_key))
            if cached is not None:
                # Return a fresh instance so we never mutate a shared
                # cached object that an in-memory test fixture might be
                # holding (the production DB-row path returns a freshly
                # built dataclass per call, which has the same effect).
                refreshed: PromptJudgement = replace(cached, cache_hit=True)
                return refreshed
        if not self._enabled:
            return PromptJudgement(
                cache_key=cache_key,
                score=None,
                error="judge_disabled",
            )
        raw = await self._call_model(prompt, ctx)
        if raw is None:
            return PromptJudgement(
                cache_key=cache_key,
                score=None,
                error="llm_unavailable",
            )
        judgement = _judgement_from_llm_response(cache_key, raw)
        if judgement.score is not None and self._cache_put is not None:
            try:
                await _maybe_await(self._cache_put(judgement))
            except Exception as exc:
                logger.warning(
                    "prompt_judge: cache put failed (key={}): {}",
                    cache_key,
                    exc,
                )
        return judgement

    async def precompute(
        self, prompts: Iterable[str], ctx: PromptJudgeContext
    ) -> dict[str, PromptJudgement]:
        """Score a batch of prompts; return a ``{prompt_text → judgement}``
        lookup the synchronous grader can consume."""
        out: dict[str, PromptJudgement] = {}
        for prompt in prompts:
            if not prompt:
                continue
            if prompt in out:
                continue  # de-dupe within a session
            out[prompt] = await self.score_one(prompt, ctx)
        return out


async def _maybe_await(value: Any) -> Any:
    """Allow cache_get/cache_put to be sync OR async. Tests pass plain
    functions; the production wiring passes async DB callbacks."""
    if hasattr(value, "__await__"):
        return await value
    return value
