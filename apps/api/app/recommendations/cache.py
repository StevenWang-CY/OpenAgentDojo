"""Read/write helpers against ``user_recommendations`` (P1-2).

Three public functions:

* :func:`load_user_history` — assembles a :class:`UserHistory` from
  Postgres for a single user. Mirrors the best-per-mission policy used
  by :func:`app.profiles.router._best_per_mission` so the radar
  aggregator and the engine read the same numbers.
* :func:`load_mission_catalogue` — assembles the typed
  :class:`MissionCandidate` list the engine needs from the
  ``missions`` + ``repo_packs`` tables.
* :func:`get_cached_or_compute` — returns the user's
  :class:`RecommendationSet`, computing on cache miss and persisting
  the result in ``user_recommendations``.
* :func:`invalidate_for_user` — stamps ``invalidated_at`` on the
  user's row so the next call recomputes.

The cache TTL is 1 hour from ``computed_at``; ``invalidated_at`` is the
explicit-flush signal raised by the grader on every new graded
submission.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.grading.attempts import candidate_beats
from app.models.mission import Mission
from app.models.repo_pack import RepoPack
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user_recommendation import UserRecommendation
from app.observability import (
    recommendation_cache_total,
    recommendation_engine_errors_total,
)
from app.recommendations.engine import (
    MissionCandidate,
    UserHistory,
    _BestAttempt,
    _PlaceholderCandidate,
    recommend,
)
from app.recommendations.schemas import (
    RecommendationDifficulty,
    RecommendationLanguage,
    RecommendationSet,
    WeakestDim,
)

# P1-2 — closed-vocabulary cast for values read out of the
# ``user_recommendations.weakest_dim`` TEXT column. We compute this engine
# output from :data:`RUBRIC_DIMENSIONS` so the only way a non-canonical
# value lands in the row is a hand-edit / older migration; defensively
# coerce unknowns to ``None`` so a single bad row can't 5xx the endpoint
# (and the next recompute will overwrite the value with a canonical one).
_VALID_WEAKEST_DIMS: frozenset[str] = frozenset(
    {
        "final_correctness",
        "verification",
        "agent_review",
        "prompt_quality",
        "context_selection",
        "safety",
        "diff_minimality",
    }
)


def _coerce_weakest_dim(raw: str | None) -> WeakestDim | None:
    """Narrow a DB-read ``weakest_dim`` to the canonical Literal or ``None``."""
    if raw is None or raw not in _VALID_WEAKEST_DIMS:
        return None
    return cast(WeakestDim, raw)


_CACHE_TTL = timedelta(hours=1)


def _dialect_name(db: AsyncSession) -> str | None:
    """Return the dialect name of the session's bound engine.

    ``AsyncSession.bind`` is ``None`` for the request-scoped session
    produced by :func:`app.db.session.get_db` (the session is created
    via ``async_sessionmaker(bind=engine)`` so the engine is registered
    on the *factory*, not the session). The correct accessor is
    :meth:`Session.get_bind`, which resolves to the engine via the
    session's identity map. That path is the only one that works in
    both runtime + migration code paths.

    Returns ``None`` only when the session is unbound (e.g. a unit test
    constructing a bare :class:`AsyncSession` without an engine), so the
    caller can take the portable SQLite-style emulated-upsert fallback.
    """
    try:
        engine = db.get_bind()
    except Exception:
        return None
    if engine is None:
        return None
    dialect = getattr(engine, "dialect", None)
    if dialect is None:
        return None
    name = getattr(dialect, "name", None)
    return str(name) if isinstance(name, str) else None


async def load_user_history(db: AsyncSession, user_id: uuid.UUID) -> UserHistory:
    """Build a :class:`UserHistory` for one user from graded sessions.

    Mirrors :func:`app.profiles.router._best_per_mission` so the
    weakest-dim argmin honours the same "uncapped beats gave-up; higher
    score wins; recency tie-break" tier policy. Pending dimensions
    (sentinel score ``-1``) are passed through as-is — the engine skips
    them.

    Tutorial sessions are explicitly excluded (P4.1 audit fix): a user
    whose only graded submission is the on-boarding tutorial should
    still read as ``graded_count == 0`` so the cold-start ladder
    surfaces instead of the regular ranking. The original join read
    ``status='graded'`` and silently included tutorial completions,
    pushing the user out of cold-start with a single sentinel score.
    """
    stmt = (
        select(
            SessionRow.id.label("session_id"),
            SessionRow.mission_id.label("mission_id"),
            SessionRow.completed_at.label("completed_at"),
            SessionRow.score.label("score"),
            Submission.score_report.label("score_report"),
            Submission.score_cap_reason.label("score_cap_reason"),
            Submission.verified.label("verified"),
        )
        .join(Submission, Submission.session_id == SessionRow.id)
        .join(Mission, Mission.id == SessionRow.mission_id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.status == "graded",
            Mission.kind != "tutorial",
        )
        .order_by(SessionRow.completed_at.desc().nulls_last(), SessionRow.id.desc())
    )
    rows = (await db.execute(stmt)).all()

    best: dict[str, _BestAttempt] = {}
    attempts_count: dict[str, int] = {}
    # Track the original best-candidate shape used by ``candidate_beats``
    # alongside the engine-shaped ``_BestAttempt`` so the comparator gets
    # exactly the fields it expects (score, completed_at, score_cap_reason,
    # score_report).
    raw_best: dict[str, dict[str, Any]] = {}
    for row in rows:
        report = row.score_report
        if isinstance(report, dict) and report.get("is_stub"):
            continue
        mid = str(row.mission_id)
        attempts_count[mid] = attempts_count.get(mid, 0) + 1
        candidate_raw = {
            "score": row.score,
            "score_report": report,
            "completed_at": row.completed_at,
            "score_cap_reason": row.score_cap_reason,
        }
        current_raw = raw_best.get(mid)
        if current_raw is None or candidate_beats(candidate_raw, current_raw):
            raw_best[mid] = candidate_raw
            dims = _extract_dimensions(report)
            best[mid] = _BestAttempt(
                mission_id=mid,
                score=int(row.score) if row.score is not None else 0,
                dimensions=dims,
                graded_at=row.completed_at,
                effective_max=_extract_effective_max(report),
            )

    return UserHistory(
        best_attempts=best,
        per_mission_attempt_count=attempts_count,
    )


def _extract_effective_max(score_report: Any) -> int:
    """Pull the per-submission ``effective_max`` from a score_report.

    Returns ``100`` on a malformed / missing report so the engine's
    pass-threshold check stays sensible against legacy rows. Mirrors
    the same defensive coercion used by
    :mod:`app.profiles.router` (which also defaults missing values to
    100 — the canonical uncapped denominator).
    """
    if not isinstance(score_report, dict):
        return 100
    raw = score_report.get("effective_max")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 100
    raw_int = int(raw)
    if raw_int <= 0:
        return 100
    return raw_int


def _extract_dimensions(score_report: Any) -> dict[str, int]:
    """Pull ``{dim: score}`` from a score_report payload.

    Returns an empty dict for malformed reports — the engine tolerates a
    missing dimension by skipping it during argmin.
    """
    if not isinstance(score_report, dict):
        return {}
    dims = score_report.get("dimensions")
    if not isinstance(dims, dict):
        return {}
    out: dict[str, int] = {}
    for dim, payload in dims.items():
        if not isinstance(payload, dict):
            continue
        raw = payload.get("score")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        if raw < 0:
            continue
        out[str(dim)] = int(raw)
    return out


async def load_mission_catalogue(db: AsyncSession) -> list[MissionCandidate]:
    """Return the published mission catalogue shaped for the engine."""
    stmt = (
        select(
            Mission.id,
            Mission.title,
            Mission.difficulty,
            Mission.kind,
            Mission.expected_weak_dim,
            Mission.tags,
            Mission.repo_pack_id,
            RepoPack.language,
        )
        .join(RepoPack, RepoPack.id == Mission.repo_pack_id)
        .where(Mission.published.is_(True))
    )
    rows = (await db.execute(stmt)).all()
    out: list[MissionCandidate] = []
    for row in rows:
        tags = tuple(row.tags or ())
        language = cast(RecommendationLanguage, row.language)
        difficulty = cast(RecommendationDifficulty, row.difficulty)
        out.append(
            MissionCandidate(
                mission_id=str(row.id),
                title=str(row.title),
                language=language,
                difficulty=difficulty,
                kind="tutorial" if row.kind == "tutorial" else "standard",
                expected_weak_dim=row.expected_weak_dim,
                tags=tags,
            )
        )
    return out


def _coming_soon_from_roadmap() -> list[_PlaceholderCandidate]:
    """Pull placeholder entries from the roadmap loader.

    Empty list when the roadmap file is absent — the "all graded" path
    degrades gracefully to a single retry recommendation.
    """
    from app.missions.roadmap import load_roadmap

    roadmap = load_roadmap()
    return [
        _PlaceholderCandidate(
            mission_id=p.id,
            title=p.title,
            language=p.language,
            target_release_date=p.target_release_date.isoformat(),
        )
        for p in roadmap.placeholders
    ]


async def get_cached_or_compute(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> RecommendationSet:
    """Return the user's recommendation set, honouring the 1-hour TTL.

    Hot path: ``invalidated_at IS NULL`` and ``computed_at >= now - 1h``
    yields a cache hit. Cache misses recompute via the pure engine,
    persist the new row, and stamp ``cache_hit=False`` on the
    response. Two concurrent callers are tolerated: the second writer's
    upsert clobbers the first row, but every reader observes a complete
    payload because the upsert is single-statement.
    """
    current = (now or datetime.now(UTC)).replace(microsecond=0)
    cutoff = current - _CACHE_TTL

    existing = (
        await db.execute(select(UserRecommendation).where(UserRecommendation.user_id == user_id))
    ).scalar_one_or_none()

    if (
        existing is not None
        and existing.invalidated_at is None
        and _ensure_utc(existing.computed_at) >= cutoff
    ):
        # P4.1 audit fix — malformed/missing ``extras`` is treated as a
        # stale cache rather than silently recomputing alignment against
        # the live (possibly drifted) catalogue. Stamping
        # ``invalidated_at`` here forces the recompute to take the
        # miss path below, which writes a canonical ``extras`` payload.
        if not _is_well_formed_extras(existing.extras):
            existing.invalidated_at = current
            await db.flush()
        else:
            catalogue = await load_mission_catalogue(db)
            rec_set = await _rebuild_from_cache(
                db=db,
                user_id=user_id,
                cached=existing,
                catalogue=catalogue,
            )
            recommendation_cache_total.labels(outcome="hit").inc()
            return rec_set

    recommendation_cache_total.labels(outcome="miss").inc()
    history = await load_user_history(db, user_id)
    catalogue = await load_mission_catalogue(db)
    coming_soon = _coming_soon_from_roadmap()
    fresh = recommend(
        user_history=history,
        mission_catalogue=catalogue,
        coming_soon=coming_soon,
        now=current,
    )
    # P1-2 §0.4 — read-through LLM polish for diagnosis + per-mission
    # ``why``. Falls back to the deterministic copy on the feature flag
    # OFF / LLM-down path; never blocks the engine output on a model
    # error.
    fresh = await _apply_prose_polish(
        db=db,
        rec_set=fresh,
        history=history,
        catalogue=catalogue,
    )
    ids = [item.mission_id for item in fresh.recommendations]
    # Persist the per-item alignment alongside the id list so a cache
    # hit can re-render the "why" copy with the original alignment
    # score — without this, the rebuild path silently re-derives
    # alignment from the user's *current* radar, which drifts between
    # the original miss and the next hit and produces non-deterministic
    # prose for the same row.
    extras = {
        "items": [
            {
                "mission_id": item.mission_id,
                "alignment": _alignment_for_mission_id(
                    item.mission_id, catalogue, fresh.weakest_dim
                ),
            }
            for item in fresh.recommendations
        ]
    }
    await _upsert_row(
        db=db,
        user_id=user_id,
        weakest_dim=fresh.weakest_dim,
        recommended_ids=ids,
        computed_at=current,
        extras=extras,
    )
    return fresh


def _alignment_for_mission_id(
    mission_id: str,
    catalogue: list[MissionCandidate],
    weakest_dim: str | None,
) -> float:
    """Re-derive alignment for ``mission_id`` against the live catalogue.

    Pulled out as a helper so :func:`get_cached_or_compute` can compute
    the alignment after the engine ran (the engine returns shaped items
    without the underlying alignment score, by design). Returns ``0.0``
    when the candidate isn't in the catalogue any more — the cache
    rebuild path treats that case as "mission unpublished" and skips it.
    """
    for cand in catalogue:
        if cand.mission_id == mission_id:
            from app.recommendations.engine import _dim_alignment_score

            return _dim_alignment_score(cand, weakest_dim)
    return 0.0


def _ensure_utc(value: datetime | None) -> datetime:
    """Normalise a possibly-naive datetime to tz-aware UTC.

    SQLite (test harness) stores ``DateTime(timezone=True)`` columns as
    tz-naive ISO strings; Postgres preserves the offset. The TTL
    comparison MUST happen in one timezone or it silently produces
    ``TypeError`` on Postgres / wrong-bucket reads on SQLite. We treat
    a naive value as UTC (the only producer that writes naive is the
    test harness, which uses ``datetime.now(UTC).replace(tzinfo=None)``
    only in legacy fixtures — the engine writes tz-aware UTC).
    """
    if value is None:
        # ``computed_at`` is NOT NULL — this is the defensive
        # "shouldn't happen" branch. Return epoch UTC so the TTL test
        # treats the row as ancient and forces a recompute.
        return datetime.fromtimestamp(0, tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _rebuild_from_cache(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    cached: UserRecommendation,
    catalogue: list[MissionCandidate],
) -> RecommendationSet:
    """Rehydrate the cached row into a full RecommendationSet.

    The persisted row carries the deterministic-ranking output
    (``weakest_dim``, the top-3 ids) plus the optional ``extras``
    payload (per-item alignment) that lets us reproduce the original
    "why" copy without re-deriving alignment against a drifted radar.
    The user's history is re-fetched (cheap; usually < 25 rows) so the
    "your attempts" + "your best score" fields stay live.
    """
    history = await load_user_history(db, user_id)
    by_id = {c.mission_id: c for c in catalogue}
    items = []
    cached_weakest = _coerce_weakest_dim(cached.weakest_dim)
    from app.recommendations.engine import FRESH_MISSION_IDS, _build_item

    # Prefer the per-item alignment persisted at engine-call time.
    # ``cached.extras`` is ``{"items": [{"mission_id":..., "alignment":...}]}``
    # when migration 0027 has populated it; older rows (or rows written
    # by code paths predating this field) fall back to recomputing.
    persisted_alignment: dict[str, float] = {}
    extras = getattr(cached, "extras", None)
    if isinstance(extras, dict):
        raw_items = extras.get("items")
        if isinstance(raw_items, list):
            for entry in raw_items:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("mission_id")
                align = entry.get("alignment")
                if isinstance(mid, str) and isinstance(align, (int, float)):
                    persisted_alignment[mid] = float(align)

    for mid in cached.recommended_ids:
        cand = by_id.get(mid)
        if cand is None:
            # Mission unpublished since the cache row landed; skip it.
            continue
        alignment = persisted_alignment.get(mid, _alignment_for(cand, cached_weakest))
        items.append(
            _build_item(
                candidate=cand,
                alignment=alignment,
                weakest_dim=cached_weakest,
                user_history=history,
                freshness_fresh=mid in FRESH_MISSION_IDS,
            )
        )
    from app.recommendations.copy import diagnosis_for

    rebuilt = RecommendationSet(
        weakest_dim=cached_weakest,
        diagnosis=diagnosis_for(cached_weakest),
        recommendations=items,
        computed_at=_ensure_utc(cached.computed_at),
        cache_hit=True,
    )
    # P2 prose-flicker fix — re-apply the SAME prose chokepoints used on
    # the miss path so the warm hit renders the polished diagnosis +
    # per-mission "why" instead of regressing to the deterministic copy.
    # The polished bytes were persisted in ``llm_cache`` at miss time, so
    # ``generate_diagnosis`` / ``generate_why`` resolve to a single SELECT
    # each here and never reach the model (verified by the prose layer's
    # read-through cache discipline). When the kill switch is OFF — or the
    # ``llm_cache`` row is somehow absent — the helpers return the same
    # deterministic copy the rebuild already produced, so the hit path
    # still matches the miss path. ``cache_hit`` is preserved as ``True``
    # because ``_apply_prose_polish`` only rewrites the prose fields.
    polished = await _apply_prose_polish(
        db=db,
        rec_set=rebuilt,
        history=history,
        catalogue=catalogue,
    )
    return polished


def _alignment_for(candidate: MissionCandidate, weakest_dim: str | None) -> float:
    from app.recommendations.engine import _dim_alignment_score

    return _dim_alignment_score(candidate, weakest_dim)


async def _upsert_row(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    weakest_dim: str | None,
    recommended_ids: Sequence[str],
    computed_at: datetime,
    extras: dict[str, Any] | None = None,
) -> None:
    """Idempotent upsert of one user's cache row.

    Uses Postgres' ``ON CONFLICT DO UPDATE`` on the primary key; for
    SQLite (test harness) we emulate via SELECT + INSERT/UPDATE because
    the SQLAlchemy ``pg_insert`` helper hard-fails on the SQLite dialect.

    ``extras`` carries the cache-rebuild fidelity payload (per-item
    alignment, freshness flag, novelty flag) so the next read can
    rehydrate the original "why" copy. The column is JSONB on Postgres
    and JSON on SQLite (see migration 0027).
    """
    dialect = _dialect_name(db)
    if dialect == "postgresql":
        stmt = pg_insert(UserRecommendation).values(
            user_id=user_id,
            weakest_dim=weakest_dim,
            recommended_ids=list(recommended_ids),
            computed_at=computed_at,
            invalidated_at=None,
            extras=extras,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "weakest_dim": weakest_dim,
                "recommended_ids": list(recommended_ids),
                "computed_at": computed_at,
                "invalidated_at": None,
                "extras": extras,
            },
        )
        await db.execute(stmt)
    else:
        existing = (
            await db.execute(
                select(UserRecommendation).where(UserRecommendation.user_id == user_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                UserRecommendation(
                    user_id=user_id,
                    weakest_dim=weakest_dim,
                    recommended_ids=list(recommended_ids),
                    computed_at=computed_at,
                    invalidated_at=None,
                    extras=extras,
                )
            )
        else:
            existing.weakest_dim = weakest_dim
            existing.recommended_ids = list(recommended_ids)
            existing.computed_at = computed_at
            existing.invalidated_at = None
            existing.extras = extras
    await db.flush()


async def invalidate_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Flag the user's cache row stale so the next call recomputes.

    Idempotent: callers that fire on every graded submission cost a
    single UPDATE on the hot path. A NULL row (user never saw a
    recommendation) is a no-op.

    Routed through the ORM-backed UPDATE on Postgres (preserves the
    native UUID column type) and through a select-then-stamp on
    SQLite (the test harness's UUID column is stored as TEXT, so a
    raw-SQL UPDATE with a UUID bind parameter errors at the driver
    boundary).

    P4.1 audit fix — failures here are counted on
    ``recommendation_engine_errors_total{stage="invalidate"}`` instead
    of silently bubbling. The caller (grader) wraps this with its own
    fail-soft guard so the grade still lands; the counter gives ops a
    signal when the cache is silently leaking stale rows.
    """
    from loguru import logger

    now = datetime.now(UTC).replace(microsecond=0)
    try:
        dialect = _dialect_name(db)
        if dialect == "postgresql":
            await db.execute(
                text("UPDATE user_recommendations SET invalidated_at = :now WHERE user_id = :uid"),
                {"now": now, "uid": str(user_id)},
            )
            recommendation_cache_total.labels(outcome="invalidated").inc()
            return
        existing = (
            await db.execute(
                select(UserRecommendation).where(UserRecommendation.user_id == user_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            return
        existing.invalidated_at = now
        await db.flush()
        recommendation_cache_total.labels(outcome="invalidated").inc()
    except Exception as exc:
        recommendation_engine_errors_total.labels(stage="invalidate").inc()
        logger.exception(
            "recommendation cache invalidate failed (user_id={}): {}",
            user_id,
            exc,
        )
        # Re-raise so the calling site can decide; the grader's wrapper
        # logs + swallows so the grade itself never fails.
        raise


async def bulk_invalidate_all(db: AsyncSession) -> int:
    """Stamp every ``user_recommendations`` row as invalidated.

    Used by the new-mission-published / rubric-rebalance one-shot
    invalidation paths (CLI in ``scripts/invalidate_recommendations.py``
    + the loader's post-upsert hook). Returns the count of rows
    affected so the operator gets a sanity check on the scope of the
    invalidation.

    Failures here are counted on
    ``recommendation_engine_errors_total{stage="bulk_invalidate"}``
    rather than silently swallowed; callers (CLI / loader) decide
    whether to bubble or log.
    """
    from loguru import logger

    now = datetime.now(UTC).replace(microsecond=0)
    try:
        dialect = _dialect_name(db)
        if dialect == "postgresql":
            result = await db.execute(
                text(
                    "UPDATE user_recommendations SET invalidated_at = :now "
                    "WHERE invalidated_at IS NULL"
                ),
                {"now": now},
            )
            await db.flush()
            # UPDATE always yields a CursorResult; narrow explicitly so
            # ``.rowcount`` type-checks (mypy sees the bare execute() return
            # as ``Result[Any]``) and a future type mismatch fails loudly
            # rather than as a stray AttributeError. Mirrors app/sessions/submit.py.
            if not isinstance(result, CursorResult):
                raise RuntimeError(
                    f"expected CursorResult from UPDATE, got {type(result).__name__}"
                )
            count = result.rowcount or 0
        else:
            rows = (
                (
                    await db.execute(
                        select(UserRecommendation).where(
                            UserRecommendation.invalidated_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.invalidated_at = now
            await db.flush()
            count = len(rows)
        recommendation_cache_total.labels(outcome="invalidated").inc()
        return int(count)
    except Exception as exc:
        recommendation_engine_errors_total.labels(stage="bulk_invalidate").inc()
        logger.exception("recommendation cache bulk_invalidate_all failed: {}", exc)
        raise


def _is_well_formed_extras(extras: Any) -> bool:
    """Return True when the persisted ``extras`` payload is shaped right.

    Today the only key we read is ``items`` — a list of
    ``{"mission_id": str, "alignment": number}`` dicts. A row missing
    that shape can't honestly rehydrate the original "why" copy, so the
    caller treats the row as stale and forces a recompute.
    """
    if not isinstance(extras, dict):
        return False
    items = extras.get("items")
    if not isinstance(items, list):
        return False
    for entry in items:
        if not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("mission_id"), str):
            return False
        align = entry.get("alignment")
        if isinstance(align, bool) or not isinstance(align, (int, float)):
            return False
    return True


async def _apply_prose_polish(
    *,
    db: AsyncSession,
    rec_set: RecommendationSet,
    history: UserHistory,
    catalogue: list[MissionCandidate],
) -> RecommendationSet:
    """Return ``rec_set`` with diagnosis + per-item ``why`` polished.

    The polish layer routes through :mod:`app.recommendations.prose`,
    which itself wraps :func:`app.llm.cache.get_or_generate`. Any
    failure path returns the deterministic copy the engine already
    produced — so this function is fail-soft by construction. Errors
    here are counted on ``recommendation_engine_errors_total{stage="prose"}``
    so dashboards can spot a silent degradation to fallback copy.
    """
    from loguru import logger

    from app.recommendations.prose import generate_diagnosis, generate_why

    if not rec_set.recommendations and rec_set.weakest_dim is None:
        # Cold-start / empty catalogue — nothing to polish.
        return rec_set

    by_id = {c.mission_id: c for c in catalogue}
    weakest_dim = rec_set.weakest_dim
    ids = [item.mission_id for item in rec_set.recommendations]

    try:
        diagnosis = await generate_diagnosis(
            db,
            weakest_dim=weakest_dim,
            recommended_mission_ids=ids,
            weakest_dim_attempts=_weakest_dim_attempts(history, weakest_dim),
            user_history=history,
        )
    except Exception as exc:  # pragma: no cover — defensive double-guard
        recommendation_engine_errors_total.labels(stage="prose").inc()
        logger.exception("recommendation prose diagnosis raised; keeping fallback: {}", exc)
        diagnosis = rec_set.diagnosis

    polished_items = []
    for item in rec_set.recommendations:
        cand = by_id.get(item.mission_id)
        if cand is None or item.status != "shipped":
            polished_items.append(item)
            continue
        try:
            why = await generate_why(
                db,
                mission_id=item.mission_id,
                weakest_dim=weakest_dim,
                failure_mode=_failure_mode_for(cand),
                expected_weak_dim=cand.expected_weak_dim,
                alignment=_alignment_for_mission_id(item.mission_id, catalogue, weakest_dim),
            )
        except Exception as exc:  # pragma: no cover — defensive double-guard
            recommendation_engine_errors_total.labels(stage="prose").inc()
            logger.exception(
                "recommendation prose why raised for mission_id={}: {}",
                item.mission_id,
                exc,
            )
            why = item.why
        polished_items.append(item.model_copy(update={"why": why}))

    return rec_set.model_copy(
        update={
            "diagnosis": diagnosis,
            "recommendations": polished_items,
        }
    )


def _weakest_dim_attempts(history: UserHistory, weakest_dim: str | None) -> int:
    """Return how many graded submissions touched ``weakest_dim``."""
    if weakest_dim is None:
        return 0
    count = 0
    for attempt in history.best_attempts.values():
        if weakest_dim in attempt.dimensions:
            count += 1
    return count


def _failure_mode_for(cand: MissionCandidate) -> str | None:
    """Pull a failure-mode tag from the candidate's ``tags`` tuple.

    The first non-``skill:`` / ``language:`` prefixed tag wins — those
    namespaces carry the failure-mode title in our manifest schema.
    Falls back to ``None`` so the prose helper renders a neutral
    placeholder.
    """
    for tag in cand.tags:
        if isinstance(tag, str) and ":" not in tag:
            return tag
    return None


__all__ = [
    "bulk_invalidate_all",
    "get_cached_or_compute",
    "invalidate_for_user",
    "load_mission_catalogue",
    "load_user_history",
]
