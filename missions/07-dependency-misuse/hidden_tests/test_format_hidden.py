"""Hidden tests for Mission 07 — Dependency Misuse (Date Formatting).

Exercises the four cases the visible suite avoids:

  * Europe/London in summer (BST)
  * Europe/London in winter (GMT)
  * Europe/London at the autumn transition Sunday
  * America/New_York in EDT season

Also checks that the patch did not add `arrow` to ``pyproject.toml`` —
the correct fix is stdlib-only.
"""

from __future__ import annotations

from pathlib import Path

from app.format import format_report_ts

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_europe_london_summer_adds_one_hour_bst() -> None:
    # 2026-06-15 12:00:00 UTC -> 2026-06-15 13:00 BST
    assert format_report_ts(1781524800, "Europe/London") == "2026-06-15 13:00"


def test_europe_london_winter_passes_through_gmt() -> None:
    # 2026-01-15 12:00:00 UTC -> 2026-01-15 12:00 GMT
    assert format_report_ts(1768478400, "Europe/London") == "2026-01-15 12:00"


def test_europe_london_transition_sunday_handles_gap() -> None:
    # 2026-10-25 01:30 BST (UTC=00:30) — still in BST before the gap
    assert format_report_ts(1792888200, "Europe/London") == "2026-10-25 01:30"
    # Same wall-clock 01:30 GMT one hour later (UTC=01:30) — after the gap
    assert format_report_ts(1792891800, "Europe/London") == "2026-10-25 01:30"


def test_america_new_york_edt_subtracts_four_hours() -> None:
    # 2026-07-04 16:00:00 UTC -> 2026-07-04 12:00 EDT
    assert format_report_ts(1783180800, "America/New_York") == "2026-07-04 12:00"


def test_no_new_third_party_dependencies_added() -> None:
    """Read the pyproject.toml to confirm `arrow` (or similar) was not added."""
    pyproject = (_BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert "arrow" not in pyproject.lower(), (
        "the fix must use stdlib `zoneinfo`; no new dep belongs in pyproject.toml"
    )
    assert "pytz" not in pyproject.lower()
