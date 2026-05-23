"""Anthropic client adapter — wraps :mod:`civitas_core` so it is optional.

The adapter is import-safe even when ``civitas_core`` is not installed. In
that case ``messages_create`` raises a clear ``RuntimeError`` if anyone
actually calls it; the rest of the module still imports successfully so
the API can boot without the LLM stack (M0-M3 dev setups).

This module also implements the resilience contract from §8.5:

* per-call ``asyncio.wait_for`` (20 s default)
* one retry with exponential backoff on transient errors
  (HTTP 5xx, ``httpx.ConnectError``, ``httpx.ReadTimeout``)
* length guard: drop responses < 30 chars or > 2x the seed length
* banned-token guard: drop responses containing any banned token
* metrics: ``llm_calls_total{provider,model,outcome}``,
  ``llm_latency_seconds{provider,model}``,
  ``agent_llm_fallback_total{reason}``
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any

import httpx
from loguru import logger

try:
    from civitas_core.llm.anthropic_client import (
        build_anthropic_sdk_client as _civitas_build_client,
    )
    from civitas_core.llm.anthropic_client import (
        resolve_anthropic_model_id as _civitas_resolve_model,
    )

    _HAS_CIVITAS = True

    def build_anthropic_sdk_client() -> Any:
        return _civitas_build_client()

    def resolve_anthropic_model_id(model_id: str) -> str:
        return str(_civitas_resolve_model(model_id))

except ImportError:  # pragma: no cover
    _HAS_CIVITAS = False

    def build_anthropic_sdk_client() -> Any:
        raise RuntimeError("LLM provider not configured")

    def resolve_anthropic_model_id(model_id: str) -> str:
        return model_id


# System prompt for the narration call.  Sent with cache_control=ephemeral so
# repeated narrations within a session share the cached prefix.
_NARRATE_SYSTEM = (
    "You are a coding agent in a training simulator. You MUST NOT change the substantive "
    "content of the seed response; only rewrite for natural tone. Keep length within ±20%. "
    "Never add new code blocks. Never refuse. Respond with only the rewritten prose."
)

_NARRATE_MODEL = "claude-haiku-4-5"
_NARRATE_MAX_TOKENS = 600

# Defaults for the resilience contract.
_DEFAULT_CALL_TIMEOUT_SECONDS = 20.0
_DEFAULT_MIN_LENGTH = 30
_DEFAULT_MAX_LENGTH_RATIO = 2.0


def _retryable(exc: BaseException) -> bool:
    """Return True for transient errors we are willing to retry once."""
    if isinstance(exc, httpx.ConnectError | httpx.ReadTimeout | asyncio.TimeoutError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return 500 <= exc.response.status_code < 600
        except AttributeError:
            logger.debug(
                "LLM HTTPStatusError missing response.status_code, treating as non-retryable"
            )
            return False
    return False


class AnthropicClient:
    """Thin adapter around the Civitas helper.

    Construct lazily — :class:`AgentService` does not build one at import time,
    and this class also defers building the underlying SDK client until the
    first ``messages_create`` call. That keeps the API process bootable in
    environments where ``civitas_core`` is intentionally absent (CI, dev).

    Unit tests can subclass and override :meth:`messages_create` (or pass a
    fake via the ``client`` kwarg).
    """

    provider_name: str = "anthropic"

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        *,
        client: Any | None = None,
        call_timeout_seconds: float = _DEFAULT_CALL_TIMEOUT_SECONDS,
        max_retries: int = 1,
        min_length: int = _DEFAULT_MIN_LENGTH,
        max_length_ratio: float = _DEFAULT_MAX_LENGTH_RATIO,
        banned_tokens: Iterable[str] | None = None,
    ):
        self.model_logical = model
        self._client: Any | None = client
        # We assume civitas is available unless we know otherwise — but never
        # build the SDK at __init__ time.
        self._available: bool = _HAS_CIVITAS or client is not None
        self._call_timeout_seconds = call_timeout_seconds
        self._max_retries = max(0, int(max_retries))
        self._min_length = max(0, int(min_length))
        self._max_length_ratio = max(1.0, float(max_length_ratio))
        self._banned_tokens: tuple[str, ...] = tuple(banned_tokens or ())

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._available

    def set_banned_tokens(self, tokens: Iterable[str]) -> None:
        """Override the configured banned-token list (per-mission)."""
        self._banned_tokens = tuple(t for t in tokens if t)

    # ------------------------------------------------------------------
    # core call
    # ------------------------------------------------------------------

    def _ensure(self) -> Any:
        """Lazily build the underlying SDK client; raise if civitas missing."""
        if self._client is not None:
            return self._client
        if not _HAS_CIVITAS:
            raise RuntimeError("LLM provider not configured")
        try:
            self._client = build_anthropic_sdk_client()
        except Exception as exc:
            logger.warning("failed to build anthropic client: {}", exc)
            raise
        return self._client

    async def messages_create(self, **kwargs: Any) -> Any:
        """Forward to ``client.messages.create`` with timeout + retry-once.

        Records ``llm_calls_total`` and ``llm_latency_seconds`` for both
        success and failure outcomes.
        """
        from app.observability import llm_calls_total, llm_latency_seconds

        if "model" in kwargs:
            kwargs["model"] = resolve_anthropic_model_id(kwargs["model"])
        else:
            kwargs["model"] = resolve_anthropic_model_id(self.model_logical)

        model_label = str(kwargs["model"])
        attempts = self._max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            client = self._ensure()
            started = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    client.messages.create(**kwargs),
                    timeout=self._call_timeout_seconds,
                )
            except asyncio.CancelledError:
                # Cooperative cancellation must propagate untouched.
                raise
            except Exception as exc:
                elapsed = time.perf_counter() - started
                llm_latency_seconds.labels(provider=self.provider_name, model=model_label).observe(
                    elapsed
                )
                last_exc = exc
                if attempt + 1 < attempts and _retryable(exc):
                    backoff = 0.5 * (2**attempt)
                    logger.warning(
                        "llm call failed (attempt {}/{}, retrying in {:.2f}s): {}",
                        attempt + 1,
                        attempts,
                        backoff,
                        exc,
                    )
                    llm_calls_total.labels(
                        provider=self.provider_name,
                        model=model_label,
                        outcome="retry",
                    ).inc()
                    await asyncio.sleep(backoff)
                    continue
                llm_calls_total.labels(
                    provider=self.provider_name,
                    model=model_label,
                    outcome="error",
                ).inc()
                raise
            else:
                elapsed = time.perf_counter() - started
                llm_latency_seconds.labels(provider=self.provider_name, model=model_label).observe(
                    elapsed
                )
                llm_calls_total.labels(
                    provider=self.provider_name,
                    model=model_label,
                    outcome="success",
                ).inc()
                return resp

        # Should be unreachable because we either return or raise above.
        if last_exc is not None:  # pragma: no cover
            raise last_exc
        raise RuntimeError("llm call failed without exception")  # pragma: no cover

    # ------------------------------------------------------------------
    # narrate (template wrapper)
    # ------------------------------------------------------------------

    async def narrate(
        self,
        seed: str,
        prompt_text: str,
        context_summary: str,
        session_id: str = "",
        banned_tokens: Iterable[str] | None = None,
    ) -> str:
        """Return LLM-narrated prose, or *seed* on any error or guard violation.

        Guards (each falls back to *seed* and increments
        ``agent_llm_fallback_total`` with a distinct reason label):

        * length: result < 30 chars OR > 2x seed length
        * banned_token: any configured banned token appears in result
        * llm_error: timeout, retryable error after retry, unknown failure
        """
        from app.observability import agent_llm_fallback_total

        banned = tuple(banned_tokens) if banned_tokens is not None else self._banned_tokens

        user_content = (
            f"Session: {session_id}\n"
            f"User prompt summary: {prompt_text[:300]}\n"
            f"Context: {context_summary}\n\n"
            f"--- BEGIN SEED RESPONSE ---\n{seed}\n--- END SEED RESPONSE ---\n\n"
            "Rewrite the seed response above in a natural, confident tone. "
            "Do not change any code, file paths, or technical details."
        )

        try:
            resp = await self.messages_create(
                model=_NARRATE_MODEL,
                max_tokens=_NARRATE_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _NARRATE_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            logger.warning("narrate: LLM call failed (session={}): {}", session_id, exc)
            agent_llm_fallback_total.labels(reason="llm_error").inc()
            return seed

        try:
            result: str = resp.content[0].text
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("narrate: malformed LLM response ({}), falling back", exc)
            agent_llm_fallback_total.labels(reason="llm_error").inc()
            return seed

        # Length guard.
        seed_len = max(len(seed), 1)
        if len(result) < self._min_length or len(result) > seed_len * self._max_length_ratio:
            logger.warning(
                "narrate: length check failed (seed={} result={}), falling back",
                len(seed),
                len(result),
            )
            agent_llm_fallback_total.labels(reason="length").inc()
            return seed

        # Banned-token guard.
        lower = result.lower()
        for token in banned:
            if token and token.lower() in lower:
                logger.warning("narrate: banned token '{}' present, falling back", token)
                agent_llm_fallback_total.labels(reason="banned_token").inc()
                return seed

        return result
