# Mission 07 — Ideal Solution

## Root cause

`format_report_ts` falls back to a hardcoded `{tz: offset_hours}` table
that ignores DST entirely. Any zone that observes DST is wrong by an
hour for half the year. The stdlib `zoneinfo` module (3.9+) ships an
IANA database and a `ZoneInfo` class that does the right thing.

## Minimal diff

```diff
--- a/backend/app/format.py
+++ b/backend/app/format.py
@@ -16,21 +16,9 @@

 from datetime import datetime, timezone
+from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


 def format_report_ts(ts: int, tz: str) -> str:
-    if tz not in _FIXED_OFFSETS_HOURS:
-        raise ValueError(...)
-    offset_hours = _FIXED_OFFSETS_HOURS[tz]
-    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
-    dt_local = dt_utc + timedelta(hours=offset_hours)
-    return dt_local.strftime("%Y-%m-%d %H:%M")
+    try:
+        zone = ZoneInfo(tz)
+    except ZoneInfoNotFoundError as exc:
+        raise ValueError(f"unknown timezone: {tz!r}") from exc
+    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(zone)
+    return dt.strftime("%Y-%m-%d %H:%M")
```

Plus a regression test that pins a DST-transition timestamp:

```python
# backend/tests/unit/test_format.py
def test_europe_london_summer_bst() -> None:
    # 2026-06-15 12:00:00 UTC -> 13:00 BST
    assert format_report_ts(1781870400, "Europe/London") == "2026-06-15 13:00"

def test_europe_london_transition_sunday() -> None:
    # 2026-10-25 02:30 UTC — clocks already gone back, still GMT
    assert format_report_ts(1793587800, "Europe/London") == "2026-10-25 02:30"
```

Net change: ~6 added lines + tests. **No new dependencies.**

## What the agent got wrong

- **Reached for `arrow`.** The library is deprecated, slow, and
  unnecessary; `zoneinfo` is in stdlib.
- **Pinned the offset from `now()`.** `arrow.utcnow().to(tz).utcoffset()`
  returns the *current* offset, then the patch applies it to a
  historical timestamp. The whole point of DST handling is that the
  offset depends on the timestamp, not on when the code runs.
- **Added a third-party dep for two lines of stdlib code.** Every new
  dependency carries supply-chain, security, and maintenance cost.

## What a strong supervisor would have prompted

- *"Use `zoneinfo` from stdlib. No new deps."*
- *"Add a test for `Europe/London` at a known BST timestamp."*
- *"What does `arrow.utcnow().to(tz).utcoffset()` return for a 2020
  timestamp run in 2026? Walk me through it."*

## Validators a careless patch trips

- `forbidden_changes.adds_arrow_dependency` and
  `forbidden_changes.imports_arrow_module` — fire on the `arrow`
  reference.
- `no_new_dependencies` — fails on the addition of `arrow` to
  `pyproject.toml`.
- `regression_test_required` — fails without a new DST-aware test.
