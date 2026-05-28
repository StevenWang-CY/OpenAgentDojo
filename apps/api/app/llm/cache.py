"""Single chokepoint every LLM-augmented surface reads/writes (P1 §0.4.1).

The :func:`get_or_generate` function below is the only place in the
codebase that should call the LLM and write to ``llm_cache``. Every
other surface (recommendation prose, scratchpad coaching, critical
moment polish, …) wraps a generator callable around its model call
and hands it here. That guarantees:

* one canonical output per ``(domain, content_hash, prompt_version)``
  tuple — the determinism contract downstream signatures depend on;
* a single observability boundary — every hit, miss, succeed, fail
  goes through the same counter discipline;
* a single fallback boundary — when the LLM is unavailable, the
  caller's deterministic fallback string is returned and the failure
  is recorded so an operator sees the breakage.

Concurrency
-----------
Writes use INSERT…ON CONFLICT (Postgres) / INSERT OR IGNORE (SQLite),
and on conflict re-SELECT the canonical row. The "first writer wins"
discipline holds: a second concurrent caller producing identical
inputs sees the first writer's bytes, not its own.

Fallback bytes are NEVER written to ``llm_cache``. Persisting a
fallback would lock the deterministic-on-replay invariant against the
moment the LLM was down, so a future retry could not heal the cache.
The fallback path emits ``llm_generation_failed_total`` so dashboards
see the degradation.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_cache import LLMCache
from app.observability import (
    llm_cache_hits_total,
    llm_generation_failed_total,
    llm_generation_latency_seconds,
    llm_generation_succeeded_total,
    llm_generation_tokens,
)


@dataclass(slots=True)
class GeneratedOutput:
    """The generator callable's return shape.

    Token counts are optional because not every code path can plumb
    them — the SDK exposes them on the live response, but a mocked
    generator in tests may legitimately set them to ``None``. When
    populated they feed the daily-budget guard documented at
    P1_DESIGN §0.4.7.
    """

    output: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(slots=True)
class CachedOutput:
    """The chokepoint's return shape.

    ``cache_hit`` is True when the bytes came from ``llm_cache``;
    False when a live generator call produced them OR when the
    fallback was returned. Inspect ``from_fallback`` to disambiguate
    the latter two cases.
    """

    output: str
    cache_hit: bool
    model_id: str
    from_fallback: bool = False


GeneratorCallable = Callable[[], Awaitable[GeneratedOutput]]


async def get_or_generate(
    db: AsyncSession,
    *,
    domain: str,
    content_hash: str,
    prompt_version: int,
    model_id: str,
    generator: GeneratorCallable,
    fallback: str | None = None,
) -> CachedOutput:
    """Return a cached LLM output or generate-and-persist one.

    Flow:
      1. SELECT on ``(domain, content_hash, prompt_version)``. On hit
         increment ``llm_cache_hits_total`` and return.
      2. On miss, call ``generator()``. On success persist the row
         (INSERT…ON CONFLICT DO NOTHING) and emit succeeded counter +
         latency + token counters. On conflict (concurrent writer
         landed first) re-SELECT to return the canonical bytes.
      3. On generator failure, emit ``llm_generation_failed_total``.
         If ``fallback`` is provided, return it with
         ``from_fallback=True`` and DO NOT persist; otherwise re-raise.
    """
    # 1. Cache lookup.
    existing = await _lookup(db, domain, content_hash, prompt_version)
    if existing is not None:
        llm_cache_hits_total.labels(
            domain=domain, prompt_version=str(prompt_version)
        ).inc()
        return CachedOutput(
            output=existing.output,
            cache_hit=True,
            model_id=existing.model_id,
        )

    # 2. Live generation.
    started = time.perf_counter()
    try:
        result = await generator()
    except Exception as exc:
        # Why we keep the generic catch: the generator wraps any of
        # httpx/asyncio/RuntimeError; the chokepoint must route each
        # to the fallback path uniformly so callers do not have to
        # re-implement the same defensiveness at every site.
        error_class = type(exc).__name__
        llm_generation_failed_total.labels(
            domain=domain, model_id=model_id, error_class=error_class
        ).inc()
        logger.warning(
            "llm cache miss + generator failed (domain={}, model={}, error={}): {}",
            domain,
            model_id,
            error_class,
            exc,
        )
        if fallback is not None:
            return CachedOutput(
                output=fallback,
                cache_hit=False,
                model_id=model_id,
                from_fallback=True,
            )
        raise

    elapsed = time.perf_counter() - started
    llm_generation_latency_seconds.labels(
        domain=domain, model_id=model_id
    ).observe(elapsed)
    llm_generation_succeeded_total.labels(
        domain=domain, model_id=model_id
    ).inc()
    if result.input_tokens is not None:
        llm_generation_tokens.labels(domain=domain, kind="input").inc(
            result.input_tokens
        )
    if result.output_tokens is not None:
        llm_generation_tokens.labels(domain=domain, kind="output").inc(
            result.output_tokens
        )

    persisted = await _persist(
        db,
        domain=domain,
        content_hash=content_hash,
        prompt_version=prompt_version,
        model_id=model_id,
        output=result.output,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return CachedOutput(
        output=persisted.output,
        cache_hit=False,
        model_id=persisted.model_id,
    )


async def _lookup(
    db: AsyncSession,
    domain: str,
    content_hash: str,
    prompt_version: int,
) -> LLMCache | None:
    """SELECT by the canonical unique key tuple; return None on miss."""
    stmt = select(LLMCache).where(
        LLMCache.domain == domain,
        LLMCache.content_hash == content_hash,
        LLMCache.prompt_version == prompt_version,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _persist(
    db: AsyncSession,
    *,
    domain: str,
    content_hash: str,
    prompt_version: int,
    model_id: str,
    output: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> LLMCache:
    """INSERT…ON CONFLICT DO NOTHING; re-SELECT on conflict.

    Postgres uses ``INSERT … ON CONFLICT DO NOTHING RETURNING *``.
    SQLite (tests) lacks that combination — the SQLAlchemy core
    insert resolves to ``INSERT OR IGNORE``, which silently no-ops on
    conflict. In both cases we follow up with a SELECT to read back
    the canonical row, so the return shape is uniform.
    """
    dialect = _dialect_name(db)
    if dialect == "postgresql":
        stmt = (
            pg_insert(LLMCache)
            .values(
                domain=domain,
                content_hash=content_hash,
                prompt_version=prompt_version,
                model_id=model_id,
                output=output,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            .on_conflict_do_nothing(
                index_elements=("domain", "content_hash", "prompt_version")
            )
        )
        await db.execute(stmt)
        await db.flush()
    else:
        # SQLite-compatible path. We rely on the unique constraint
        # raising IntegrityError on a true conflict. Use a SAVEPOINT
        # (``begin_nested``) so a conflict only unwinds *this* insert
        # instead of poisoning the request-scoped transaction — without
        # the savepoint we'd ``rollback()`` everything the route has
        # staged before us and the dependency's request-end commit
        # would crash with ``PendingRollbackError``.
        try:
            async with db.begin_nested():
                row = LLMCache(
                    domain=domain,
                    content_hash=content_hash,
                    prompt_version=prompt_version,
                    model_id=model_id,
                    output=output,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                db.add(row)
                await db.flush()
        except IntegrityError as exc:
            # Concurrent writer landed first — the savepoint rolled back
            # so the outer transaction is intact; the re-SELECT below
            # returns the canonical bytes.
            logger.debug(
                "llm cache insert conflict on ({}, {}…, v={}): {} — re-selecting",
                domain,
                content_hash[:8],
                prompt_version,
                exc,
            )
    # NOTE: we deliberately do NOT call ``await db.commit()`` here.
    # The ``get_db`` dependency owns the commit lifecycle of a request-
    # scoped session — an inline commit here would land partial work
    # from other handlers running on the same session and break the
    # "request is one atomic transaction" contract. ``flush()`` above
    # is enough to make the row visible to the follow-up SELECT.
    refreshed = await _lookup(db, domain, content_hash, prompt_version)
    if refreshed is None:  # pragma: no cover — defensive
        raise RuntimeError(
            f"llm_cache row vanished after insert (domain={domain!r}, "
            f"content_hash={content_hash[:8]}…, prompt_version={prompt_version})"
        )
    return refreshed


def _dialect_name(db: AsyncSession) -> str:
    """Best-effort dialect lookup; mirrors :func:`app.sessions.notes._dialect_name`.

    ``db.bind`` can be ``None`` when the session has no engine attached
    (some test harnesses bind lazily), and ``db.get_bind()`` raises in
    that case rather than returning ``None``. Catching the failure and
    defaulting to ``postgresql`` keeps the production path untouched
    while letting the tests exercise the SQLite branch.
    """
    try:
        engine = db.get_bind()
    except Exception:
        return "postgresql"
    if engine is None:
        return "postgresql"
    dialect = getattr(engine, "dialect", None)
    if dialect is None:
        return "postgresql"
    name = getattr(dialect, "name", None)
    return str(name) if isinstance(name, str) else "postgresql"
