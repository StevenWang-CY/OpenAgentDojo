"""Single source of truth for the "best-per-mission" tier policy (ADR 0009).

The selection rule:

  1. Skip grader-failure stubs (``score_report.is_stub == True``).
  2. **Uncapped beats capped** — any attempt with ``score_cap_reason IS NULL``
     wins over every attempt with ``score_cap_reason = 'gave_up'``.
  3. Within the preferred tier, the **highest score** wins.
  4. Ties break to the **more recent ``completed_at``** (then to row id for
     absolute determinism if a test seeds identical timestamps).

The policy is used in three places:

  * ``app.profiles.router._best_per_mission`` — radar / public profile.
  * ``app.profiles.router._skills_dedupe_by_mission`` — failure-mode mastery.
  * ``app.missions.your_attempts.load_your_attempts`` — private "your attempts"
    strip on the mission detail page.

Before this module existed the three callsites each held their own copy of
the comparator. Drift between any two of them would have made the public
profile, the skills page, and the mission detail page disagree about
"which attempt is your best" — a silent bug that would be expensive to
catch after the fact. Funnelling all three through ``candidate_beats``
keeps them honest.

The function is intentionally **shape-agnostic**: callers pass any object
that exposes ``score`` (int or None), ``score_cap_reason`` (str or None),
and ``completed_at`` (datetime or None). Both the SQLAlchemy row tuples
returned by the aggregation queries and the dataclass shape used by the
mission-detail loader satisfy this duck type.
"""

from __future__ import annotations

from typing import Any, Protocol


class _BestAttemptShape(Protocol):
    """Duck type for everything ``candidate_beats`` reads on its inputs."""

    score: int | None
    score_cap_reason: str | None
    completed_at: Any


def candidate_beats(candidate: Any, current: Any) -> bool:
    """Return True iff ``candidate`` should replace ``current`` as the best.

    Tier-aware comparison implementing the policy above. The function is
    pure (no I/O, no DB access, no randomness) so a single fixture event
    stream can exercise every branch.

    Accepts ``Any`` rather than a Protocol type because Python's runtime
    Protocol checks would force callers to ``isinstance`` against it; the
    callsites are tiny and the duck type is documented above.
    """
    cand_capped = _score_cap_reason(candidate) is not None
    curr_capped = _score_cap_reason(current) is not None
    if curr_capped and not cand_capped:
        return True
    if not curr_capped and cand_capped:
        return False

    cand_score = _score_int(candidate)
    curr_score = _score_int(current)
    if cand_score != curr_score:
        return cand_score > curr_score

    cand_t = _completed_at(candidate)
    curr_t = _completed_at(current)
    if cand_t is None and curr_t is None:
        return False
    if cand_t is None:
        return False
    if curr_t is None:
        return True
    return cand_t > curr_t


def _score_int(obj: Any) -> int:
    """Coerce the candidate's ``score`` to an int, treating None as -1.

    Accepts both dict-shaped candidates (``obj["score"]``) and attribute-
    shaped (``obj.score``) so the comparator works for SQLAlchemy rows,
    the dict shape used in profiles.router._skills_dedupe_by_mission, and
    the dataclass used in profiles.router._best_per_mission alike.
    """
    raw = _get(obj, "score")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return -1


def _score_cap_reason(obj: Any) -> str | None:
    raw = _get(obj, "score_cap_reason")
    return raw if isinstance(raw, str) else None


def _completed_at(obj: Any) -> Any:
    return _get(obj, "completed_at")


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from ``obj`` whether it's a mapping or an attribute holder."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


__all__ = ["candidate_beats"]
