"""Anthropic client wiring for the LLM substrate (P1 §0.4.1).

This module reuses :class:`app.agent.llm.AnthropicClient` for actual
model calls — that adapter already implements the resilience contract
(per-call ``asyncio.wait_for``, retry-once on transient errors, outer
wall-clock budget) and the Civitas/Bedrock optionality. Reimplementing
any of that here would drift from the agent code and double the
surface that needs to be kept honest.

What this module adds on top:

  * :func:`build_anthropic_client` — convenience constructor that
    returns a fresh :class:`AnthropicClient` for a logical model id.
    Lazy: the underlying SDK is built on first ``messages_create``.
  * :func:`resolve_model_id` — pure-function shim around the existing
    Civitas resolver, kept here so callers in ``app.llm.*`` don't
    have to import from ``app.agent.llm`` directly.

Env vars (same as ``keys.md`` and ``civitas_core``):

* ``ANTHROPIC_PROVIDER``      — ``'bedrock'`` or unset.
* ``AWS_BEARER_TOKEN_BEDROCK``— opaque Bedrock bearer token.
* ``AWS_REGION``              — e.g. ``'us-east-2'``.
* ``ANTHROPIC_API_KEY``       — direct-Anthropic fallback for dev.
"""

from __future__ import annotations

from app.agent.llm import AnthropicClient as _AnthropicClient
from app.agent.llm import resolve_anthropic_model_id as _resolve_anthropic_model_id


def build_anthropic_client(
    logical_model_id: str,
    *,
    call_timeout_seconds: float = 20.0,
    max_retries: int = 1,
) -> _AnthropicClient:
    """Return a fresh :class:`AnthropicClient` bound to ``logical_model_id``.

    The returned client lazily builds its SDK connection on first
    ``messages_create`` call; instantiation is import-safe even when
    ``civitas_core`` is unavailable (the call itself raises
    ``RuntimeError`` — the cache layer treats that as a generator
    failure and routes to the deterministic fallback).
    """
    return _AnthropicClient(
        model=logical_model_id,
        call_timeout_seconds=call_timeout_seconds,
        max_retries=max_retries,
    )


def resolve_model_id(logical_id: str) -> str:
    """Resolve a logical model id (e.g. ``'claude-haiku-4-5'``) to the
    inference-profile id Bedrock expects.

    Delegates to :func:`app.agent.llm.resolve_anthropic_model_id` so
    this repo carries exactly one resolver. Returns the input string
    unchanged when ``civitas_core`` is not installed (the agent module
    documents the same fallback for the local-dev path).
    """
    return _resolve_anthropic_model_id(logical_id)
