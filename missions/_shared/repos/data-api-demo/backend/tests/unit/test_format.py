"""Visible unit tests for ``app.format.format_report_ts``.

Cover the easy cases (UTC + a non-DST-affected midwinter Tokyo timestamp).
The DST boundary cases that catch the broken implementation live in the
hidden suite (Mission 07).
"""

from __future__ import annotations

import pytest

from app.format import format_report_ts


def test_utc_passthrough() -> None:
    # 2026-01-15 12:00:00 UTC
    assert format_report_ts(1768478400, "UTC") == "2026-01-15 12:00"


def test_tokyo_offset_no_dst() -> None:
    # Japan has no DST — fixed +09:00 year-round, so the broken
    # fallback happens to be correct here.
    # 2026-01-15 12:00:00 UTC -> 2026-01-15 21:00 in Asia/Tokyo
    assert format_report_ts(1768478400, "Asia/Tokyo") == "2026-01-15 21:00"


def test_unknown_tz_raises() -> None:
    with pytest.raises(ValueError):
        format_report_ts(1768478400, "Mars/Olympus_Mons")
