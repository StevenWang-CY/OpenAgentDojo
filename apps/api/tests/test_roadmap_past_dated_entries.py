"""Past-dated entries must not poison the rest of the roadmap (P4.1).

The roadmap is consumed by the catalog endpoint when the client passes
``?include=upcoming``; a single forgotten placeholder dated for last
week previously raised ``ValidationError`` at load time and took the
whole catalog surface down. The fix filters past-dated entries
**before** Pydantic validation, logs each dropped entry at WARNING, and
keeps the remaining (future-dated) entries intact.

This file proves the contract:

  * a roadmap with ONE past-dated entry and ONE future-dated entry
    yields a non-empty Roadmap (the future entry survives);
  * a roadmap with ONLY past-dated entries yields an empty Roadmap
    (degrade gracefully, never raise);
  * a roadmap with malformed-shape entries still degrades to empty
    without raising.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

from app.missions.roadmap import load_roadmap


def _write_roadmap(tmp_path: Path, body: str) -> Path:
    """Write a roadmap.yaml at ``tmp_path`` and return its path."""
    target = tmp_path / "roadmap.yaml"
    target.write_text(dedent(body), encoding="utf-8")
    return target


def test_past_dated_entry_dropped_future_kept(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    past = (today - timedelta(days=7)).isoformat()
    future = (today + timedelta(days=90)).isoformat()
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
            teaser: shipping in a quarter or so once the design is locked in
            language: typescript
        """,
    )

    roadmap = load_roadmap(target)

    assert len(roadmap.placeholders) == 1, (
        "past-dated entry must be dropped but future entry must remain"
    )
    assert roadmap.placeholders[0].id == "fresh-mission"


def test_only_past_dated_entries_yields_empty_roadmap(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    past = (today - timedelta(days=14)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: ancient-mission
            title: Truly past its sell-by date
            target_release_date: {past}
            teaser: this was on the books and never landed
            language: go
        """,
    )

    roadmap = load_roadmap(target)

    assert roadmap.placeholders == [], (
        "an all-past roadmap must degrade to an empty list, not raise"
    )


def test_malformed_yaml_does_not_raise(tmp_path: Path) -> None:
    today = datetime.now(UTC).date()
    future = (today + timedelta(days=30)).isoformat()
    target = _write_roadmap(
        tmp_path,
        f"""
        placeholders:
          - id: missing-fields
            language: python
          - id: ok-mission
            title: A perfectly fine roadmap entry
            target_release_date: {future}
            teaser: a normal mission entry that should land in a month
            language: python
        """,
    )

    roadmap = load_roadmap(target)
    # One entry is malformed (missing title / target_release_date / teaser).
    # The whole load degrades to empty rather than raising — this is the
    # explicit "keep the catalog up" contract.
    assert roadmap.placeholders == []
