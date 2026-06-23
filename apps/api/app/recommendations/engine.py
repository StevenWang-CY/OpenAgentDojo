"""Pure ranking layer of the next-mission recommendation engine (P1-2).

The contract is documented in [P1_DESIGN.md §P1-2](../../../P1_DESIGN.md):

    Given the same ``(user_history, mission_catalogue, RUBRIC_VERSION)``,
    :func:`recommend` returns the same ``RecommendationSet`` — including
    the per-mission "why" strings and the diagnosis copy.

The engine does **no** I/O. The caller (typically
:mod:`app.recommendations.router` or
:mod:`app.recommendations.cache`) is responsible for loading the user's
best-per-mission submissions and the published mission catalogue, then
handing both into :func:`recommend`. The router-side wrapper layer reads
from Postgres; the engine itself is functional.

The deterministic ranking is built from four signal components:

* ``dim_alignment``    — 1.0 if the mission's ``expected_weak_dim``
  equals the user's weakest dim; 0.5 if a failure-mode tag on the
  mission maps to the user's weakest dim; 0.0 otherwise.
* ``difficulty_match`` — 1.0 if the mission's difficulty equals the
  user's current band; 0.5 if one band off; 0.0 otherwise.
* ``freshness``        — 1.0 if the mission is in the canonical
  :data:`FRESH_MISSION_IDS` set (the three Go missions shipped with
  P1-1); 0.0 otherwise.
* ``novelty_bonus``    — 0.5 if the user has never attempted the
  mission; 0.0 if they have one or more graded attempts already.

Ties on the total score break to the mission id in ascending lexical
order, which keeps the algorithm replay-stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal

from app.grading.dimensions import RUBRIC_DIMENSIONS
from app.recommendations.copy import (
    ALL_GRADED_DIAGNOSIS,
    COLD_START_DIAGNOSIS,
    diagnosis_for,
    why_for_mission,
)
from app.recommendations.schemas import (
    RecommendationDifficulty,
    RecommendationItem,
    RecommendationLanguage,
    RecommendationSet,
)

# ---------------------------------------------------------------------------
# Tuning knobs — bumped together when the algorithm changes.
# ---------------------------------------------------------------------------

# The rubric version this engine knows about. Bumped when the ranking
# table or its inputs change; consumers that cache prose key off the
# version so a rebalance never resurrects stale "why" copy.
RUBRIC_VERSION: Final[int] = 1

# Pass threshold: a mission whose best score is at or above this is
# considered "passed" and excluded from the recommendation set unless the
# user has graded *everything*. We compare against ``70% of the effective
# max for that submission`` — the same percent-of-effective-max semantics
# used by :func:`app.profiles.router._best_per_mission` so the radar
# aggregator and the engine treat the same submission as
# "passed" vs "still needs work". The raw int below is kept as a
# back-compat constant (some callers / re-exports rely on it) but the
# load-bearing test is :func:`_passed`.
PASS_THRESHOLD: Final[int] = 70
_PASS_FRACTION: Final[float] = 0.7


def _passed(score: int, effective_max: int) -> bool:
    """Return True when ``score`` clears the canonical pass threshold.

    Centralises the ``score >= max(1, int(effective_max * 0.7))``
    semantics used at the mastery surface. Two upstream call-sites
    historically diverged — one applied the percent rule, the other
    compared against the raw integer 70 — so a 35/50-capped submission
    appeared "not passed" by the engine while the profile counted it
    as a pass. Centralising the math here eliminates that drift.
    """
    if not isinstance(score, int):
        return False
    if not isinstance(effective_max, int) or effective_max <= 0:
        # Defensive: a missing / zero effective_max means we have no
        # signal to compare against. Treat as not-passed so the mission
        # stays eligible — false negatives recover on the next attempt;
        # a false positive would silently demote a mission.
        return False
    threshold = max(1, int(effective_max * _PASS_FRACTION))
    return score >= threshold


# Cold-start ladder (mission ids 01, 02, 03 in the canonical numbering).
# A user with zero graded submissions is shown these as the introductory
# path. Hardcoded so the engine doesn't depend on mission-row ordering
# (which would be a non-deterministic surface across deployments).
INTRODUCTORY_LADDER: Final[tuple[str, str, str]] = (
    "auth-cookie-expiration",
    "agent-wrong-file",
    "missing-regression-test",
)

# "Fresh" missions — published after the P1-1 backfill. Hardcoded
# because the Mission table has no ``published_at`` timestamp and using
# row order would silently drift. Updated explicitly when a new mission
# pack ships.
FRESH_MISSION_IDS: Final[frozenset[str]] = frozenset(
    {"goroutine-leak", "context-cancel-dropped", "error-shadowed-by-wrap"}
)

# Failure-mode-tag → rubric-dimension mapping. Used to compute the 0.5
# dim_alignment branch: a mission whose failure-mode tag maps to the
# user's weakest dimension is *indirectly* relevant even when its
# ``expected_weak_dim`` is something else. The mapping is hand-curated,
# documented inline, and stays stable across replays (it is part of the
# rubric, not derived from data — so bumping it requires an ADR and a
# ``RUBRIC_VERSION`` bump).
FAILURE_MODE_TO_DIM: Final[dict[str, str]] = {
    "checks_presence_not_expiration": "agent_review",
    "overfitted_visible_test": "final_correctness",
    "wrong_layer_committed": "context_selection",
    "missing_regression_test": "verification",
    "race_condition": "verification",
    "context_dropped": "agent_review",
    "error_wrapped_swallowed": "agent_review",
    "dependency_misuse": "safety",
    "security_check_removed": "safety",
    "typecheck_ignored": "verification",
    "api_contract_drift": "prompt_quality",
    "excessive_rewrite": "diff_minimality",
    "goroutine_leak": "verification",
    # P1-7 — second mission wave (14-20). Each maps to the dimension the
    # mission is primarily designed to exercise (its ``expected_weak_dim``).
    "optimistic_update_no_rollback": "final_correctness",
    "zod_any_escape": "final_correctness",
    "pandas_iterrows_perf_trap": "final_correctness",
    "pydantic_silent_coercion": "final_correctness",
    "channel_deadlock_on_cancel": "safety",
    "asyncio_shield_misuse": "safety",
    "sql_tx_leak_early_return": "safety",
}

# Difficulty bands. Drives the ``difficulty_match`` signal.
_DIFFICULTY_ORDER: Final[tuple[str, ...]] = (
    "beginner",
    "intermediate",
    "advanced",
)

# Canonical RUBRIC_DIMENSIONS order — for argmin tie-break on the weakest
# dimension.
_RUBRIC_ORDER: Final[tuple[str, ...]] = tuple(name for name, _ in RUBRIC_DIMENSIONS)


# ---------------------------------------------------------------------------
# Input dataclasses — what the caller must hand to ``recommend``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BestAttempt:
    """One user-best graded attempt against a single mission.

    The "your attempts: N" rendering on the recommendation card reads
    from :attr:`UserHistory.per_mission_attempt_count`, not from a per-
    attempt count on this dataclass — the engine ranks by best score and
    weakest dim, not by how many times the user has tried. We used to
    carry an ``attempts`` field here too but it was a dead surface: the
    ranking never consumed it, the cache-extras payload never persisted
    it, and the cache hydrator backfilled it from
    ``per_mission_attempt_count`` anyway. Removed to shrink the
    invariant surface and remove a tempting "use ``best.attempts``"
    foot-gun for future callers.
    """

    mission_id: str
    score: int
    dimensions: dict[str, int] = field(default_factory=dict)
    graded_at: datetime | None = None
    # P4.1 audit fix — effective_max is the per-submission denominator
    # the engine uses to decide "passed". Defaults to 100 so older test
    # fixtures (which omit the field) keep behaving like uncapped
    # scoring. When the cache loader has the report it threads the live
    # value through so the engine and the profile mastery surface treat
    # the same submission identically.
    effective_max: int = 100


@dataclass(frozen=True, slots=True)
class UserHistory:
    """The slice of a user's history the engine needs.

    ``best_attempts`` maps ``mission_id`` → best-uncapped-attempt for that
    mission (the profile aggregator already computes this with the same
    "uncapped beats gave-up" tier policy — see
    :func:`app.profiles.router._best_per_mission`). ``per_mission_attempt_count``
    counts every graded submission (including gave-up attempts) so the
    response shape can render "Your attempts: N".
    """

    best_attempts: dict[str, _BestAttempt] = field(default_factory=dict)
    per_mission_attempt_count: dict[str, int] = field(default_factory=dict)

    @property
    def graded_count(self) -> int:
        return len(self.best_attempts)

    @property
    def last_graded_at(self) -> datetime | None:
        timestamps = [a.graded_at for a in self.best_attempts.values() if a.graded_at is not None]
        if not timestamps:
            return None
        return max(timestamps)


@dataclass(frozen=True, slots=True)
class MissionCandidate:
    """The slice of a mission row the engine needs.

    The shape is intentionally minimal — pass exactly this and the engine
    can rank without reaching back to the DB. ``language`` is derived
    from the repo pack at the caller site so the engine never has to
    materialise the repo-packs catalog.
    """

    mission_id: str
    title: str
    language: RecommendationLanguage
    difficulty: RecommendationDifficulty
    kind: Literal["standard", "tutorial"]
    expected_weak_dim: str | None
    tags: tuple[str, ...]
    # P1-2 freshness signal. ``created_at`` is the catalog row's
    # creation timestamp; when present we compare it against the user's
    # last graded-at timestamp to decide if the mission is "new since
    # they last played" (the freshness signal in the ranking table).
    # ``None`` falls back to the hardcoded :data:`FRESH_MISSION_IDS`
    # set so legacy callers / tests that don't carry the column still
    # produce a stable ranking.
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _PlaceholderCandidate:
    """A coming-soon placeholder surfaced when every mission is graded."""

    mission_id: str
    title: str
    language: RecommendationLanguage
    target_release_date: str


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def recommend(
    *,
    user_history: UserHistory,
    mission_catalogue: list[MissionCandidate],
    coming_soon: list[_PlaceholderCandidate] | None = None,
    now: datetime | None = None,
) -> RecommendationSet:
    """Rank up to three next missions for a signed-in user.

    Deterministic by contract: same inputs produce byte-identical output,
    including diagnosis + per-mission "why" strings. ``now`` is injected
    for replay tests; production callers leave it ``None`` and the
    function stamps ``datetime.now(UTC)``.

    The function returns a fully-shaped :class:`RecommendationSet` ready
    to serialise. ``cache_hit`` is always ``False`` here — the caller
    sets it to ``True`` after pulling a stored row from the
    ``user_recommendations`` table.

    The whole body is wrapped in the
    :data:`app.observability.recommendation_compute_seconds` histogram
    so the engine's wall-clock budget shows up in dashboards.
    """
    from app.observability import recommendation_compute_seconds

    with recommendation_compute_seconds.time():
        return _recommend_inner(
            user_history=user_history,
            mission_catalogue=mission_catalogue,
            coming_soon=coming_soon,
            now=now,
        )


def _recommend_inner(
    *,
    user_history: UserHistory,
    mission_catalogue: list[MissionCandidate],
    coming_soon: list[_PlaceholderCandidate] | None,
    now: datetime | None,
) -> RecommendationSet:
    """Pure-function ranking core; see :func:`recommend` for the contract."""
    computed_at = (now or datetime.now(UTC)).replace(microsecond=0)
    catalogue = _filter_standard(mission_catalogue)

    # Cold-start: no graded submissions OR no usable mission catalogue.
    if user_history.graded_count == 0:
        return _cold_start(catalogue, computed_at)

    weakest_dim = _argmin_weakest_dim(user_history)
    user_band = _current_band(user_history)

    candidates = _eligible_candidates(catalogue, user_history)

    # Everyone-passed edge: user has graded every shipped mission, all
    # at or above the pass threshold. Surface the largest-gap-to-ideal
    # plus two coming-soon placeholders.
    if not candidates and len(user_history.best_attempts) > 0:
        return _all_graded(
            catalogue=catalogue,
            user_history=user_history,
            coming_soon=coming_soon or [],
            computed_at=computed_at,
        )

    last_graded_at = user_history.last_graded_at
    scored: list[tuple[float, MissionCandidate, float, float, float, float]] = []
    for candidate in candidates:
        dim_alignment = _dim_alignment_score(candidate, weakest_dim)
        difficulty_match = _difficulty_match_score(candidate, user_band)
        freshness = _freshness_score(candidate, last_graded_at)
        novelty_bonus = (
            0.5 if user_history.per_mission_attempt_count.get(candidate.mission_id, 0) == 0 else 0.0
        )
        total = dim_alignment + difficulty_match + freshness + novelty_bonus
        scored.append((total, candidate, dim_alignment, difficulty_match, freshness, novelty_bonus))

    # Sort by score desc, then mission_id asc for replay stability.
    scored.sort(key=lambda t: (-t[0], t[1].mission_id))
    top = scored[:3]

    items = [
        _build_item(
            candidate=tup[1],
            alignment=tup[2],
            weakest_dim=weakest_dim,
            user_history=user_history,
            freshness_fresh=_is_fresh(tup[1], last_graded_at),
        )
        for tup in top
    ]
    return RecommendationSet(
        weakest_dim=weakest_dim,
        diagnosis=diagnosis_for(weakest_dim),
        recommendations=items,
        computed_at=computed_at,
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# Cold-start path.
# ---------------------------------------------------------------------------


def _cold_start(
    catalogue: list[MissionCandidate],
    computed_at: datetime,
) -> RecommendationSet:
    by_id = {m.mission_id: m for m in catalogue}
    items: list[RecommendationItem] = []
    for mid in INTRODUCTORY_LADDER:
        cand = by_id.get(mid)
        if cand is None:
            # Mission not in the catalogue (test env with a slimmed-down
            # catalogue). Skip it deterministically.
            continue
        items.append(
            RecommendationItem(
                mission_id=cand.mission_id,
                title=cand.title,
                language=cand.language,
                difficulty=cand.difficulty,
                why=("kicks off the introductory ladder — earns your first dimension scores."),
                your_best_score=None,
                your_attempts=0,
                status="shipped",
            )
        )
    return RecommendationSet(
        weakest_dim=None,
        diagnosis=COLD_START_DIAGNOSIS,
        recommendations=items,
        computed_at=computed_at,
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# All-graded edge.
# ---------------------------------------------------------------------------


def _all_graded(
    *,
    catalogue: list[MissionCandidate],
    user_history: UserHistory,
    coming_soon: list[_PlaceholderCandidate],
    computed_at: datetime,
) -> RecommendationSet:
    # Pick the mission with the largest gap-to-ideal (lowest best score)
    # and tie-break on ascending mission_id so the choice is stable.
    ranked: list[tuple[int, str, MissionCandidate]] = []
    for cand in catalogue:
        best = user_history.best_attempts.get(cand.mission_id)
        score = best.score if best is not None else 0
        ranked.append((score, cand.mission_id, cand))
    if not ranked:
        return RecommendationSet(
            weakest_dim=None,
            diagnosis=ALL_GRADED_DIAGNOSIS,
            recommendations=[],
            computed_at=computed_at,
            cache_hit=False,
        )
    ranked.sort(key=lambda t: (t[0], t[1]))
    _, _, retry_target = ranked[0]
    items: list[RecommendationItem] = [
        _build_item(
            candidate=retry_target,
            alignment=1.0,
            weakest_dim=None,
            user_history=user_history,
            mode="all_graded",
        )
    ]
    for ph in coming_soon[:2]:
        items.append(
            RecommendationItem(
                mission_id=ph.mission_id,
                title=ph.title,
                language=ph.language,
                difficulty="intermediate",
                why=(
                    "coming soon — placeholder slot on the roadmap. "
                    "We will surface it the moment it ships."
                ),
                your_best_score=None,
                your_attempts=0,
                status="coming_soon",
                target_release_date=ph.target_release_date,
            )
        )
    return RecommendationSet(
        weakest_dim=None,
        diagnosis=ALL_GRADED_DIAGNOSIS,
        recommendations=items,
        computed_at=computed_at,
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# Scoring helpers.
# ---------------------------------------------------------------------------


def _filter_standard(catalogue: list[MissionCandidate]) -> list[MissionCandidate]:
    """Drop tutorials and sort by mission_id for replay stability."""
    standard = [m for m in catalogue if m.kind == "standard"]
    return sorted(standard, key=lambda m: m.mission_id)


def _eligible_candidates(
    catalogue: list[MissionCandidate], user_history: UserHistory
) -> list[MissionCandidate]:
    """Return the missions the user hasn't passed yet.

    "Passed" = the submission's ``score`` clears 70% of its
    ``effective_max`` (see :func:`_passed`). Missions the user has
    never attempted are always eligible.
    """
    out: list[MissionCandidate] = []
    for cand in catalogue:
        best = user_history.best_attempts.get(cand.mission_id)
        if best is None or not _passed(best.score, best.effective_max):
            out.append(cand)
    return out


def is_all_graded_set(user_history: UserHistory, mission_catalogue: list[MissionCandidate]) -> bool:
    """Return True when ``recommend`` would take the all-graded branch.

    Reproduces the exact branch condition in :func:`_recommend_inner`
    (graded *something* and nothing eligible remains) so the cache layer
    can label a persisted row as all-graded without re-running the whole
    engine. Keeping the predicate here — beside the branch it mirrors —
    means the cache rebuild stays byte-identical to the cold compute even
    if the eligibility rule moves.
    """
    if user_history.graded_count == 0:
        return False
    catalogue = _filter_standard(mission_catalogue)
    candidates = _eligible_candidates(catalogue, user_history)
    return not candidates and len(user_history.best_attempts) > 0


def _argmin_weakest_dim(user_history: UserHistory) -> str | None:
    """Return the user's weakest measured dimension or ``None`` if none.

    Averages each dimension across the user's best-per-mission attempts.
    Pending dimensions (score < 0) are skipped — they carry no signal.
    Ties break to the canonical RUBRIC_DIMENSIONS order so the engine
    output is byte-stable.
    """
    if not user_history.best_attempts:
        return None
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for attempt in user_history.best_attempts.values():
        for dim, score in attempt.dimensions.items():
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            if score < 0:
                continue
            sums[dim] = sums.get(dim, 0.0) + float(score)
            counts[dim] = counts.get(dim, 0) + 1
    ratios: list[tuple[float, int, str]] = []
    for idx, dim in enumerate(_RUBRIC_ORDER):
        count = counts.get(dim, 0)
        if count == 0:
            continue
        max_s = next(m for n, m in RUBRIC_DIMENSIONS if n == dim)
        avg = sums[dim] / count
        ratios.append((avg / max_s, idx, dim))
    if not ratios:
        return None
    ratios.sort(key=lambda t: (t[0], t[1]))
    return ratios[0][2]


def _current_band(user_history: UserHistory) -> RecommendationDifficulty:
    """Bucket the user's avg total score into a difficulty band."""
    scores = [a.score for a in user_history.best_attempts.values()]
    if not scores:
        return "beginner"
    avg = sum(scores) / len(scores)
    if avg < 50:
        return "beginner"
    if avg < 80:
        return "intermediate"
    return "advanced"


def _dim_alignment_score(candidate: MissionCandidate, weakest_dim: str | None) -> float:
    """Return ``dim_alignment`` per the engine spec.

    Three branches: direct (1.0), failure-mode-tag-mapped (0.5), or none
    (0.0). ``weakest_dim is None`` (all-strong radar) collapses to 0.0 so
    the freshness / novelty signals drive the ranking.
    """
    if weakest_dim is None:
        return 0.0
    if candidate.expected_weak_dim == weakest_dim:
        return 1.0
    for tag in candidate.tags:
        mapped = FAILURE_MODE_TO_DIM.get(tag)
        if mapped == weakest_dim:
            return 0.5
    return 0.0


def _freshness_score(candidate: MissionCandidate, last_graded_at: datetime | None) -> float:
    """Return the freshness signal for ``candidate``.

    Preferred path: a typed ``created_at`` is threaded through from the
    catalog loader. We score the mission as fresh (1.0) when its
    creation timestamp is strictly after the user's most-recent graded
    timestamp — i.e. it landed *since they last played* and is genuinely
    new to them.

    Fallback path: when ``created_at`` is ``None`` we fall back to the
    hardcoded :data:`FRESH_MISSION_IDS` set so the engine keeps a
    stable signal during the migration window. Once every catalog
    loader threads ``created_at`` the constant can be retired.
    """
    if candidate.created_at is not None:
        if last_graded_at is None:
            # The user has graded *something* (we're past the cold-start
            # branch) but no usable timestamp survived — treat the
            # mission as not-fresh to avoid double-counting against
            # novelty_bonus.
            return 0.0
        return 1.0 if candidate.created_at > last_graded_at else 0.0
    # Legacy fallback while older callers / tests still populate
    # MissionCandidate without a ``created_at``.
    return 1.0 if candidate.mission_id in FRESH_MISSION_IDS else 0.0


def _is_fresh(candidate: MissionCandidate, last_graded_at: datetime | None) -> bool:
    """Boolean form of :func:`_freshness_score` used by the copy layer."""
    return _freshness_score(candidate, last_graded_at) > 0.0


def _difficulty_match_score(
    candidate: MissionCandidate, user_band: RecommendationDifficulty
) -> float:
    """Bucket-distance score: 1.0 exact, 0.5 one off, 0.0 two off."""
    try:
        c_idx = _DIFFICULTY_ORDER.index(candidate.difficulty)
        u_idx = _DIFFICULTY_ORDER.index(user_band)
    except ValueError:
        return 0.0
    distance = abs(c_idx - u_idx)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.5
    return 0.0


# ---------------------------------------------------------------------------
# Item builder.
# ---------------------------------------------------------------------------


def _build_item(
    *,
    candidate: MissionCandidate,
    alignment: float,
    weakest_dim: str | None,
    user_history: UserHistory,
    override_why: str | None = None,
    freshness_fresh: bool | None = None,
    mode: Literal["normal", "all_graded"] = "normal",
) -> RecommendationItem:
    """Materialise a :class:`RecommendationItem` from one candidate.

    ``freshness_fresh`` lets the caller short-circuit the freshness
    inference; when omitted we derive it from
    :data:`FRESH_MISSION_IDS`. ``mode`` propagates to the copy layer
    so the all-graded retry-target gets its own copy branch (see
    :func:`why_for_mission`).
    """
    best = user_history.best_attempts.get(candidate.mission_id)
    attempts = user_history.per_mission_attempt_count.get(candidate.mission_id, 0)
    if freshness_fresh is not None:
        is_fresh = freshness_fresh
    elif candidate.created_at is not None:
        # Caller didn't pre-compute the flag but the candidate carries
        # a typed timestamp; the freshness check needs ``last_graded_at``
        # to compare against.
        is_fresh = _is_fresh(candidate, user_history.last_graded_at)
    else:
        is_fresh = candidate.mission_id in FRESH_MISSION_IDS
    why = override_why or why_for_mission(
        mission_id=candidate.mission_id,
        expected_weak_dim=candidate.expected_weak_dim,
        weakest_dim=weakest_dim,
        alignment=alignment,
        freshness_fresh=is_fresh,
        mode=mode,
    )
    return RecommendationItem(
        mission_id=candidate.mission_id,
        title=candidate.title,
        language=candidate.language,
        difficulty=candidate.difficulty,
        why=why,
        your_best_score=best.score if best is not None else None,
        your_attempts=attempts,
        status="shipped",
    )
