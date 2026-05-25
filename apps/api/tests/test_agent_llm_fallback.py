"""Tests for the LLM resilience contract in ``app.agent.llm``.

Verifies that:

* a too-short response triggers the length guard and returns the seed,
  incrementing ``agent_llm_fallback_total{reason="length"}``
* a transient ``httpx.ReadTimeout`` is retried exactly once with backoff;
  if it keeps failing, ``narrate`` returns the seed and increments
  ``agent_llm_fallback_total{reason="llm_error"}``
* a response containing a banned token returns the seed and increments
  ``agent_llm_fallback_total{reason="banned_token"}``
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.agent.llm import AnthropicClient
from app.observability import REGISTRY


def _fallback_count(reason: str) -> float:
    val = REGISTRY.get_sample_value("agent_llm_fallback_total", {"reason": reason})
    return float(val or 0.0)


def _calls_count(outcome: str, model: str = "claude-haiku-4-5") -> float:
    val = REGISTRY.get_sample_value(
        "llm_calls_total",
        {"provider": "anthropic", "model": model, "outcome": outcome},
    )
    return float(val or 0.0)


def _content_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


class _ShortResponseClient:
    """Fake SDK client that returns a tiny response."""

    def __init__(self) -> None:
        self.messages = self
        self.calls = 0

    async def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        return _content_response("ok")  # under 30 chars


class _BannedTokenClient:
    def __init__(self, text: str) -> None:
        self.messages = self
        self.calls = 0
        self._text = text

    async def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        return _content_response(self._text)


class _TimeoutThenOk:
    """Raise on the first call, succeed on the retry."""

    def __init__(self, recovery_text: str) -> None:
        self.messages = self
        self.calls = 0
        self._text = recovery_text

    async def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("simulated timeout")
        return _content_response(self._text)


class _AlwaysTimeout:
    def __init__(self) -> None:
        self.messages = self
        self.calls = 0

    async def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        raise httpx.ReadTimeout("simulated timeout")


SEED = (
    "Thanks — I read through the requireAuth middleware and the cookie "
    "expiration check is missing. I'll add an isValid() call and a regression test."
)


@pytest.mark.asyncio
async def test_length_guard_falls_back_to_seed() -> None:
    before = _fallback_count("length")
    fake = _ShortResponseClient()
    client = AnthropicClient(client=fake)
    out = await client.narrate(seed=SEED, prompt_text="fix", context_summary="x")
    assert out == SEED
    assert fake.calls == 1
    assert _fallback_count("length") == before + 1


@pytest.mark.asyncio
async def test_banned_token_triggers_fallback() -> None:
    """A response containing a banned token must return the seed."""
    before = _fallback_count("banned_token")
    # The fake response is long enough to pass the length guard.
    text = SEED.replace("isValid()", "DO_NOT_USE_THIS_TOKEN call instead")
    fake = _BannedTokenClient(text)
    client = AnthropicClient(client=fake, banned_tokens=["do_not_use_this_token"])
    out = await client.narrate(seed=SEED, prompt_text="fix", context_summary="x")
    assert out == SEED
    assert _fallback_count("banned_token") == before + 1


@pytest.mark.asyncio
async def test_transient_timeout_is_retried_once() -> None:
    """Timeout on first attempt, success on the retry → return the LLM text."""
    before_retry = _calls_count("retry")
    before_success = _calls_count("success")
    fake = _TimeoutThenOk(recovery_text=SEED)  # exact-length retry text
    client = AnthropicClient(client=fake)
    out = await client.narrate(seed=SEED, prompt_text="fix", context_summary="x")
    assert out == SEED  # length-equal, so passes
    assert fake.calls == 2
    assert _calls_count("retry") == before_retry + 1
    assert _calls_count("success") == before_success + 1


@pytest.mark.asyncio
async def test_persistent_timeout_falls_back_to_seed() -> None:
    """If the retry also times out, the seed is returned and llm_error fires."""
    before_error = _calls_count("error")
    before_fallback = _fallback_count("llm_error")
    fake = _AlwaysTimeout()
    client = AnthropicClient(client=fake)
    out = await client.narrate(seed=SEED, prompt_text="fix", context_summary="x")
    assert out == SEED
    assert fake.calls == 2  # original + 1 retry
    assert _calls_count("error") == before_error + 1
    assert _fallback_count("llm_error") == before_fallback + 1


@pytest.mark.asyncio
async def test_outer_budget_short_circuits_retry_loop() -> None:
    """P1-2: the outer wall-clock budget bounds total retry time.

    A pathological slow remote that keeps timing out within the per-attempt
    window can otherwise burn ``call_timeout * (retries + 1) + sum(backoffs)``
    seconds, well past what the caller expects. Configure a tight outer
    budget + a generous per-attempt timeout so the first retry's
    "projected = elapsed + backoff + per_attempt" exceeds the budget and
    the loop aborts after a single attempt.
    """
    before_error = _calls_count("error")
    before_retry = _calls_count("retry")
    fake = _AlwaysTimeout()
    # ``call_timeout_seconds=10`` is the per-attempt cap (well above the
    # 0.5s the first backoff would add). ``outer_timeout_seconds=1`` is
    # tighter than (any elapsed) + 0.5s backoff + 10s per-attempt, so
    # the projection at retry-check time MUST trip the guard.
    client = AnthropicClient(
        client=fake,
        call_timeout_seconds=10.0,
        outer_timeout_seconds=1.0,
        max_retries=3,
    )
    with pytest.raises(httpx.ReadTimeout):
        await client.messages_create(messages=[{"role": "user", "content": "hi"}])
    # Exactly one attempt: the budget guard fires BEFORE the second.
    assert fake.calls == 1
    assert _calls_count("error") == before_error + 1
    # No retry was scheduled — the guard fired in the same iteration.
    assert _calls_count("retry") == before_retry


@pytest.mark.asyncio
async def test_no_civitas_raises_runtime_error_with_clear_message() -> None:
    """When civitas is absent and no test client is injected, calls must fail loudly."""
    client = AnthropicClient()  # no override → _client is None
    # civitas_core isn't installed in CI; ``_HAS_CIVITAS`` is False here.
    from app.agent import llm as llm_mod

    if llm_mod._HAS_CIVITAS:
        pytest.skip("civitas_core is installed in this environment")

    assert client.is_available() is False
    with pytest.raises(RuntimeError, match="LLM provider not configured"):
        await client.messages_create(messages=[{"role": "user", "content": "hi"}])
