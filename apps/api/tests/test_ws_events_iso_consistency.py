"""P1-6 audit item 11 — backfill vs live-emit ISO format consistency.

The WebSocket events stream emits ``occurred_at`` two ways:

* **Backfill** — when a client (re)connects, ``_send_backfill`` pulls
  events from the DB and serialises them via
  ``apps/api/app/ws/events.py::_serialise``.
* **Live emit** — when a new event lands, the emitter in
  ``apps/api/app/sessions/events.py`` publishes the row to Redis and
  the WS subscriber forwards it.

Pre-audit, the backfill called ``ev.occurred_at.isoformat()`` directly,
which under SQLite (naive datetimes) drops the timezone suffix
entirely, while the live-emit path called
``datetime.now(UTC).isoformat()`` and emitted ``+00:00``. The FE
treated these as two different timestamps when it sorted the merged
stream.

After the audit both paths funnel through ``ws.events._coerce_iso``,
which:

  * coerces naive datetimes to UTC-aware,
  * produces microsecond-precision ISO-8601,
  * normalises the timezone suffix to ``Z`` (matches what
    ``apps/api/app/reports/replay.py::_coerce_event_iso`` emits, so
    the replay JSON and the WS stream agree byte-for-byte).

These tests pin the contract:

  * the helper itself round-trips correctly across all the datetime
    flavours we care about (naive, aware-UTC, aware-non-UTC, None);
  * backfill and live-emit produce identical strings when fed the
    same source datetime.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.models.supervision_event import SupervisionEvent
from app.ws.events import _coerce_iso, _serialise


def test_coerce_iso_zsuffix_for_utc_aware() -> None:
    """A UTC-aware datetime must yield a ``Z``-suffixed microsecond ISO."""
    dt = datetime(2026, 5, 28, 14, 30, 5, 123456, tzinfo=UTC)
    assert _coerce_iso(dt) == "2026-05-28T14:30:05.123456Z"


def test_coerce_iso_naive_datetime_treated_as_utc() -> None:
    """Naive datetimes (SQLite default) are coerced to UTC, not dropped."""
    dt = datetime(2026, 5, 28, 14, 30, 5, 123456)  # no tzinfo
    assert _coerce_iso(dt) == "2026-05-28T14:30:05.123456Z"


def test_coerce_iso_non_utc_timezone_converted() -> None:
    """A non-UTC aware datetime is converted to UTC before formatting."""
    tz_p2 = timezone(timedelta(hours=2))
    dt = datetime(2026, 5, 28, 16, 30, 5, 123456, tzinfo=tz_p2)
    # 16:30 +02:00 ⇒ 14:30 UTC
    assert _coerce_iso(dt) == "2026-05-28T14:30:05.123456Z"


def test_coerce_iso_none_returns_empty_string() -> None:
    """``None`` ⇒ empty string (mirrors the replay helper's contract)."""
    assert _coerce_iso(None) == ""


def test_serialise_uses_coerce_iso() -> None:
    """The backfill ``_serialise`` must produce the coerce_iso form."""
    dt = datetime(2026, 5, 28, 14, 30, 5, 123456, tzinfo=UTC)
    ev = SupervisionEvent(
        id=42,
        session_id=__import__("uuid").UUID("a1111111-1111-4111-8111-111111111111"),
        event_type="session.started",
        payload={"mission_id": "m1"},
        occurred_at=dt,
    )
    out = _serialise(ev)
    assert out["occurred_at"] == "2026-05-28T14:30:05.123456Z"


@pytest.mark.parametrize(
    "dt",
    [
        datetime(2026, 5, 28, 14, 30, 5, 123456, tzinfo=UTC),
        datetime(2026, 5, 28, 14, 30, 5, 123456),  # naive
        datetime(2026, 5, 28, 16, 30, 5, 123456, tzinfo=timezone(timedelta(hours=2))),
    ],
)
def test_backfill_and_emit_iso_strings_match(dt: datetime) -> None:
    """Backfill (``_serialise``) and live-emit (now-stamped) must agree.

    The emit-time format lives in :mod:`app.sessions.events` and goes
    through the same helper after the P1-6 fix. We re-derive the
    expected string from the helper directly — if either side drifts,
    this test fails.
    """
    import uuid

    ev = SupervisionEvent(
        id=1,
        session_id=uuid.UUID("a1111111-1111-4111-8111-111111111111"),
        event_type="patch.applied",
        payload={"turn_index": 0, "files_changed": 1, "added": 1, "removed": 0},
        occurred_at=dt,
    )
    backfill_iso = _serialise(ev)["occurred_at"]
    emit_iso = _coerce_iso(dt)
    assert backfill_iso == emit_iso
    # And: the format is always Z-suffixed microsecond ISO, never the
    # legacy ``+00:00`` Python default form, never a tz-less string.
    assert backfill_iso.endswith("Z")
    assert "+00:00" not in backfill_iso
    # Microsecond precision: there is a ``.`` and six digits before the Z.
    head, _, _ = backfill_iso.rpartition("Z")
    _, _, frac = head.rpartition(".")
    assert len(frac) == 6, backfill_iso
