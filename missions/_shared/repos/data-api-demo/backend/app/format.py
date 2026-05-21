"""Report timestamp formatting (Mission 07).

The contract (see also docs/reporting.md): given an epoch second timestamp
``ts`` and an IANA timezone string ``tz``, return the timestamp formatted
as ``YYYY-MM-DD HH:mm`` in *that* zone, **including DST adjustments**.

The shipped implementation is wrong: it applies a fixed UTC offset
without consulting any DST table, so an October-November transition in
``Europe/London`` is off by one hour. The agent's typical "fix" is to
install ``arrow`` and use ``arrow.get(ts).to(tz)`` — which is also wrong
because the codepath the agent writes pins the offset from the
*current* moment, not from ``ts``. The correct fix uses
``zoneinfo.ZoneInfo`` from the stdlib.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Lookup table the broken implementation falls back to. Real timezone
#: handling lives in ``zoneinfo`` — see ``ideal_solution.md``.
_FIXED_OFFSETS_HOURS: dict[str, int] = {
    "UTC": 0,
    "Europe/London": 0,   # BUG: ignores BST (+1) and the Oct/Mar transitions
    "Europe/Paris": 1,    # BUG: ignores CEST (+2)
    "America/New_York": -5,  # BUG: ignores EDT (-4) and the Mar/Nov transitions
    "Asia/Tokyo": 9,
}


def format_report_ts(ts: int, tz: str) -> str:
    """Format ``ts`` (epoch seconds) as ``YYYY-MM-DD HH:mm`` in ``tz``.

    Args:
        ts: Epoch seconds (UTC).
        tz: IANA timezone string, e.g. ``"Europe/London"``.

    Returns:
        A naive ``YYYY-MM-DD HH:mm`` string in the local wall-clock of ``tz``.

    Raises:
        ValueError: if ``tz`` is unknown to the (intentionally incomplete)
            fallback table.
    """
    if tz not in _FIXED_OFFSETS_HOURS:
        raise ValueError(
            f"unknown timezone: {tz!r}. The fixed-offset fallback only knows "
            f"{sorted(_FIXED_OFFSETS_HOURS)}; teach the formatter to use "
            "zoneinfo instead."
        )
    offset_hours = _FIXED_OFFSETS_HOURS[tz]
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt_local = dt_utc + timedelta(hours=offset_hours)
    return dt_local.strftime("%Y-%m-%d %H:%M")
