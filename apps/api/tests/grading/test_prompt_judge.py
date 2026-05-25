"""LLM-judge prompt scoring (P0-1).

The judge replaces a substring keyword scorer that rewarded any 80-char
prompt containing the right magic words. These tests pin:

* the SHA-256 cache key is stable across processes
* the LLM response parser tolerates malformed JSON without crashing
* the cache short-circuits the LLM call on hit
* an unavailable LLM with a cold cache yields a *pending* judgement
  (score=None), not a fabricated zero or seven
* the score engine consumes judgements and surfaces per-axis signals
* a pending dimension reduces the total's effective max from 100 to 90
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.grading.diff import ParsedDiff
from app.grading.prompt_judge import (
    PROMPT_QUALITY_MAX_SCORE,
    RUBRIC_VERSION,
    PromptJudge,
    PromptJudgeContext,
    PromptJudgement,
    _judgement_from_llm_response,
    compute_cache_key,
)
from app.grading.score import _score_prompt_quality, compute_score
from app.grading.validators.tests_pass import TestRunResult

# ---------------------------------------------------------------------------
# Cache-key determinism
# ---------------------------------------------------------------------------


def test_cache_key_is_stable_across_calls() -> None:
    ctx = PromptJudgeContext(mission_id="auth-cookie-expiration")
    k1 = compute_cache_key("review the diff please", ctx)
    k2 = compute_cache_key("review the diff please", ctx)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_cache_key_changes_with_rubric_version_via_mission_revision() -> None:
    """Bumping mission_revision forces re-judgement."""
    ctx_a = PromptJudgeContext(mission_id="m", mission_revision="1")
    ctx_b = PromptJudgeContext(mission_id="m", mission_revision="2")
    assert compute_cache_key("p", ctx_a) != compute_cache_key("p", ctx_b)


def test_cache_key_changes_with_prompt_text() -> None:
    ctx = PromptJudgeContext(mission_id="m")
    assert compute_cache_key("a", ctx) != compute_cache_key("b", ctx)


# ---------------------------------------------------------------------------
# Response parser tolerance
# ---------------------------------------------------------------------------


def test_parser_handles_valid_response() -> None:
    raw = json.dumps(
        {
            "specificity": 2.0,
            "constraint": 1.5,
            "engagement": 0.5,
            "verifiability": 2.5,
            "rationale": "names the file, defines success criterion",
        }
    )
    j = _judgement_from_llm_response("k", raw)
    assert j.score == round(2.0 + 1.5 + 0.5 + 2.5)
    assert j.error is None
    assert j.specificity == 2.0


def test_parser_clamps_out_of_range_axes() -> None:
    raw = json.dumps(
        {
            "specificity": 99.0,  # clamped to 2.5
            "constraint": -1.0,  # clamped to 0.0
            "engagement": 2.5,
            "verifiability": 2.5,
            "rationale": "ok",
        }
    )
    j = _judgement_from_llm_response("k", raw)
    assert j.specificity == 2.5
    assert j.constraint == 0.0
    assert j.score == round(2.5 + 0.0 + 2.5 + 2.5)


def test_parser_returns_pending_on_invalid_json() -> None:
    j = _judgement_from_llm_response("k", "not json")
    assert j.score is None
    assert j.error is not None and "invalid_json" in j.error


def test_parser_returns_pending_on_non_object() -> None:
    j = _judgement_from_llm_response("k", "[1, 2, 3]")
    assert j.score is None
    assert j.error == "response_not_object"


# ---------------------------------------------------------------------------
# Judge end-to-end with fake client + memory cache
# ---------------------------------------------------------------------------


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContent(text)]


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def messages_create(self, **kwargs: Any) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(self._text)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_judge_uses_cache_on_repeat_call() -> None:
    payload = json.dumps(
        {
            "specificity": 2.5,
            "constraint": 2.5,
            "engagement": 2.5,
            "verifiability": 2.5,
            "rationale": "ideal prompt",
        }
    )
    client = _FakeClient(payload)
    store: dict[str, PromptJudgement] = {}

    def cache_get(k: str) -> PromptJudgement | None:
        return store.get(k)

    def cache_put(j: PromptJudgement) -> None:
        store[j.cache_key] = j

    judge = PromptJudge(client=client, cache_get=cache_get, cache_put=cache_put)
    ctx = PromptJudgeContext(mission_id="m")

    first = _run(judge.score_one("the prompt", ctx))
    second = _run(judge.score_one("the prompt", ctx))

    assert client.calls == 1, "second call must hit the cache, not the model"
    assert first.score == second.score == PROMPT_QUALITY_MAX_SCORE
    assert second.cache_hit is True
    assert first.cache_hit is False


def test_judge_returns_pending_when_llm_unavailable() -> None:
    class _BrokenClient:
        async def messages_create(self, **_: Any) -> Any:
            raise RuntimeError("LLM provider not configured")

    store: dict[str, PromptJudgement] = {}
    judge = PromptJudge(
        client=_BrokenClient(),
        cache_get=store.get,
        cache_put=lambda j: store.__setitem__(j.cache_key, j),
    )
    ctx = PromptJudgeContext(mission_id="m")
    j = _run(judge.score_one("anything", ctx))
    assert j.score is None
    assert j.error == "llm_unavailable"
    assert j.cache_key not in store, "pending judgements must not pollute the cache"


def test_disabled_judge_yields_pending_without_calling_model() -> None:
    client = _FakeClient(json.dumps({"specificity": 2.5}))
    judge = PromptJudge(client=client, enabled=False)
    ctx = PromptJudgeContext(mission_id="m")
    j = _run(judge.score_one("p", ctx))
    assert j.score is None
    assert j.error == "judge_disabled"
    assert client.calls == 0


def test_precompute_dedupes_repeated_prompts() -> None:
    payload = json.dumps(
        {
            "specificity": 1.0,
            "constraint": 1.0,
            "engagement": 1.0,
            "verifiability": 1.0,
            "rationale": "ok",
        }
    )
    client = _FakeClient(payload)
    store: dict[str, PromptJudgement] = {}
    judge = PromptJudge(
        client=client,
        cache_get=store.get,
        cache_put=lambda j: store.__setitem__(j.cache_key, j),
    )
    ctx = PromptJudgeContext(mission_id="m")
    result = _run(judge.precompute(["same prompt", "same prompt", "other"], ctx))
    assert set(result.keys()) == {"same prompt", "other"}
    assert client.calls == 2


# ---------------------------------------------------------------------------
# Score engine integration
# ---------------------------------------------------------------------------


@dataclass
class _RewardSignals:
    prompt_quality: Any | None = None


@dataclass
class _ExpectedContext:
    required: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    discouraged: list[str] = field(default_factory=list)


@dataclass
class _HiddenTests:
    suites: list[str] = field(default_factory=list)


@dataclass
class _Repo:
    test_commands: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Manifest:
    id: str = "judge-integration"
    version: str = "1"
    expected_files: list[str] = field(default_factory=list)
    expected_diff_lines_p50: int = 20
    expected_context: _ExpectedContext = field(default_factory=_ExpectedContext)
    reward_signals: _RewardSignals = field(default_factory=_RewardSignals)
    hidden_tests: _HiddenTests = field(default_factory=_HiddenTests)
    repo: _Repo = field(default_factory=_Repo)


def _judge_lookup_for(prompts: list[str], score: int) -> dict[str, PromptJudgement]:
    out: dict[str, PromptJudgement] = {}
    for p in prompts:
        out[p] = PromptJudgement(
            cache_key="cache-" + p,
            score=score,
            specificity=score / 4.0,
            constraint=score / 4.0,
            engagement=score / 4.0,
            verifiability=score / 4.0,
            rationale="",
        )
    return out


def test_score_uses_judge_when_lookup_provided() -> None:
    manifest = _Manifest()
    turns = [
        {"turn_index": 0, "user_prompt": "a"},
        {"turn_index": 1, "user_prompt": "b"},
        {"turn_index": 2, "user_prompt": "c"},
    ]
    judgements = _judge_lookup_for(["a", "b", "c"], score=8)
    ds = _score_prompt_quality(turns, manifest, judgements)
    assert ds.score == 8
    assert any("via LLM judge" in s for s in ds.signals)


def test_score_pending_when_no_judgements_resolve() -> None:
    manifest = _Manifest()
    turns = [{"turn_index": 0, "user_prompt": "anything"}]
    judgements = {"anything": PromptJudgement(cache_key="k", score=None, error="llm_unavailable")}
    ds = _score_prompt_quality(turns, manifest, judgements)
    assert ds.pending is True
    assert ds.score == -1
    assert any("prompt_quality_pending" in s for s in ds.signals)


def test_compute_score_excludes_pending_from_total() -> None:
    """A pending prompt-quality dimension must not be summed into the total
    as a zero — the total's effective max drops to 90."""
    manifest = _Manifest(
        hidden_tests=_HiddenTests(suites=["hidden"]),
        expected_files=["src/foo.ts"],
    )
    diff = ParsedDiff(
        "--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1,1 +1,2 @@\n const x = 1;\n+const y = 2;\n"
    )
    test_results = [
        TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=4),
    ]
    turns = [{"turn_index": 0, "user_prompt": "p"}]
    pending = {"p": PromptJudgement(cache_key="k", score=None, error="llm_unavailable")}
    report = compute_score(
        diff=diff,
        events=[],
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
        agent_turns=turns,
        prompt_judgements=pending,
    )
    # final_correctness = 12+8+6+4 = 30; verification, agent_review,
    # context_selection, safety all 0 in this stripped fixture; minimality
    # = 10. Pending prompt_quality contributes 0 to total, max is 90.
    pq = report.dimensions["prompt_quality"]
    assert pq.pending is True
    assert pq.to_dict()["score"] is None
    # Total excludes pq.
    expected_total = (
        30
        + 10
        + sum(
            ds.score
            for name, ds in report.dimensions.items()
            if name not in {"final_correctness", "diff_minimality", "prompt_quality"}
            and not ds.pending
        )
    )
    assert report.total == expected_total


def test_fallback_keyword_scorer_runs_when_no_judgements() -> None:
    """Backward-compat: tests / environments that don't pass a judgement
    lookup keep working via the legacy substring scorer."""
    manifest = _Manifest()
    turns = [{"turn_index": 0, "user_prompt": "fix it"}]
    ds = _score_prompt_quality(turns, manifest, None)
    # Pre-P0-1 keyword path scored "fix it" as -2 (vague) -> clamped to 0.
    assert ds.score == 0
    assert any("keyword fallback" in s for s in ds.signals)


def test_replay_is_deterministic_with_same_judgements() -> None:
    """The judge cache makes replays byte-identical."""
    manifest = _Manifest(hidden_tests=_HiddenTests(suites=["hidden"]))
    diff = ParsedDiff("--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1 +1,2 @@\n x\n+y\n")
    test_results = [
        TestRunResult(suite="hidden", exit_code=0, stdout="", stderr="", passed=1),
    ]
    judgements = _judge_lookup_for(["p"], score=7)
    turns = [{"turn_index": 0, "user_prompt": "p"}]
    r1 = compute_score(
        diff=diff,
        events=[],
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
        agent_turns=turns,
        prompt_judgements=judgements,
    )
    r2 = compute_score(
        diff=diff,
        events=[],
        validator_results=[],
        test_results=test_results,
        manifest=manifest,
        agent_turns=turns,
        prompt_judgements=judgements,
    )
    assert r1.total == r2.total
    assert r1.dimensions["prompt_quality"].score == r2.dimensions["prompt_quality"].score


def test_rubric_version_is_pinned() -> None:
    """A change to RUBRIC_VERSION is intentional and must be paired with a
    cache-invalidation plan; this test catches accidental bumps."""
    assert RUBRIC_VERSION == 1
