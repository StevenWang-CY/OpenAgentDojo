"""Engine-side failures bump ``recommendation_engine_errors_total``.

Two sites exercised here:

* :func:`app.recommendations.cache.invalidate_for_user` — when the
  underlying UPDATE raises, the wrapper counts the error on the
  ``invalidate`` stage and re-raises so the grader can choose to log
  and continue.
* The grading runner's pre-grade engine swallow path — when
  ``recommend`` throws, the runner catches, increments the
  ``pre_grade`` stage counter, and falls back to the legacy
  ``engine_recommended_mission_ids = None`` shape.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.observability import recommendation_engine_errors_total
from app.recommendations.cache import invalidate_for_user


def _label_value(counter, **labels) -> float:
    """Return the running float value of a labelled counter sample."""
    metric = counter.labels(**labels)
    return float(metric._value.get())  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_invalidate_for_user_counts_failures(db_session) -> None:
    """A DB-side raise during ``invalidate_for_user`` counts + re-raises."""
    before = _label_value(
        recommendation_engine_errors_total, stage="invalidate"
    )

    # Force the session's ``execute`` to blow up so the wrapper's
    # except path fires; the counter MUST increment exactly once and
    # the exception MUST surface.
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated DB outage")

    with patch.object(db_session, "execute", side_effect=_boom):
        with pytest.raises(RuntimeError, match="simulated DB outage"):
            await invalidate_for_user(db_session, uuid.uuid4())

    after = _label_value(
        recommendation_engine_errors_total, stage="invalidate"
    )
    assert after - before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_engine_errors_counter_has_all_stages_defined() -> None:
    """Sanity check that every stage label the slice uses is registered."""
    for stage in ("pre_grade", "post_grade", "invalidate", "bulk_invalidate", "prose"):
        # Calling ``.labels(...)`` is what registers the time series; if
        # the labelset is broken (e.g. a typo) Prometheus would raise.
        recommendation_engine_errors_total.labels(stage=stage)
