"""``load_roadmap(strict=...)`` contract (P4.1B remediation).

Strict mode is invoked by the CI gate (``scripts/validate_missions.py``)
so a forgotten past-dated placeholder fails the build instead of silently
dropping at runtime. Lenient mode (the default) is the API runtime
posture — past-dated placeholders are filtered out with a WARNING log
and a :data:`roadmap_past_dated_dropped_total` counter increment, so the
catalog surface stays up while ops sees the drift on dashboards.

This file proves the three-way contract:

  * lenient = drop + counter increment + warning (no raise);
  * strict = :class:`RoadmapPastDatedError`;
  * full-collapse from past-dated drops triggers a "roadmap collapsed
    to empty" WARNING (lenient only) without raising.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

import pytest

from app.missions.roadmap import (
    RoadmapPastDatedError,
    load_roadmap,
)
from app.observability import roadmap_past_dated_dropped_total


def _write_roadmap(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "roadmap.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


def _counter_value(mission_id: str) -> float:
    """Read the current counter value for ``mission_id`` (0 if unseen)."""
    return roadmap_past_dated_dropped_total.labels(mission_id=mission_id)._value.get()  # type: ignore[attr-defined]


def test_strict_mode_raises_on_past_dated_entry(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    past = (today - timedelta(days=7)).isoformat()
    future = (today + timedelta(days=30)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: stale-mission
            title: An old idea that should not ship
            target_release_date: {past}
            teaser: this should have shipped already and was forgotten
            language: python
          - id: fresh-mission
            title: A mission still on the roadmap
            target_release_date: {future}
            teaser: shipping in a quarter once the design is locked in
            language: typescript
        """,
    )

    with pytest.raises(RoadmapPastDatedError) as excinfo:
        load_roadmap(target, strict=True)

    # Error message mentions the offending id so CI logs are actionable.
    assert "stale-mission" in str(excinfo.value)


def test_lenient_mode_drops_past_dated_and_bumps_counter(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    past = (today - timedelta(days=14)).isoformat()
    future = (today + timedelta(days=60)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: lenient-drop-mission
            title: A placeholder dated for two weeks ago
            target_release_date: {past}
            teaser: was supposed to ship a fortnight ago and never did
            language: go
          - id: lenient-keep-mission
            title: A future placeholder that survives
            target_release_date: {future}
            teaser: still on the roadmap for next quarter
            language: python
        """,
    )

    before = _counter_value("lenient-drop-mission")
    roadmap = load_roadmap(target)  # strict defaults to False
    after = _counter_value("lenient-drop-mission")

    assert len(roadmap.placeholders) == 1
    assert roadmap.placeholders[0].id == "lenient-keep-mission"
    # Exactly one drop should have been counted for the offending id.
    assert after - before == pytest.approx(1.0)


def test_lenient_mode_warns_on_full_collapse(tmp_path: Path, caplog) -> None:
    """Past-dated drops that empty the entire roadmap log a louder WARNING.

    The per-entry drop already logs at WARNING; this is the separate
    "we lost ALL entries" signal that points at a roadmap.yaml the
    rotation forgot to refresh.
    """
    import logging

    today = datetime.now(UTC).date()
    past = (today - timedelta(days=5)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: collapse-a
            title: Only-past placeholder A
            target_release_date: {past}
            teaser: this entry slipped past its target date
            language: python
          - id: collapse-b
            title: Only-past placeholder B
            target_release_date: {past}
            teaser: this entry also slipped past its target date
            language: typescript
        """,
    )

    # loguru -> caplog bridge — register a handler at module load time so
    # the warning produced by the loader's loguru sink lands in caplog.
    from loguru import logger as _loguru

    handler_id = _loguru.add(
        lambda message: caplog.handler.emit(
            logging.LogRecord(
                name="loguru",
                level=logging.WARNING,
                pathname=__file__,
                lineno=0,
                msg=str(message),
                args=(),
                exc_info=None,
            )
        ),
        level="WARNING",
    )
    try:
        with caplog.at_level(logging.WARNING):
            roadmap = load_roadmap(target)
    finally:
        _loguru.remove(handler_id)

    assert roadmap.placeholders == []
    # Either the per-entry drop OR the collapse-to-empty message must be
    # observable in the captured warning stream; assert the collapse
    # message specifically since that's the load-bearing new signal.
    captured = " ".join(record.message for record in caplog.records)
    assert "collapsed to empty" in captured


def test_strict_mode_passes_for_future_only_roadmap(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    future = (today + timedelta(days=21)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: future-only-mission
            title: A future-only roadmap with one entry
            target_release_date: {future}
            teaser: shipping in three weeks once review lands
            language: typescript
        """,
    )

    # Strict mode must succeed on a clean roadmap.
    roadmap = load_roadmap(target, strict=True)
    assert len(roadmap.placeholders) == 1
    assert roadmap.placeholders[0].id == "future-only-mission"
