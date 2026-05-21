# Report timestamps

`app.format.format_report_ts(ts, tz)` is the single place that converts
a stored UTC epoch second into a local wall-clock string for a report.
The required output shape is `YYYY-MM-DD HH:mm`.

## Why DST is the whole point

The shipped implementation maps each timezone to a *fixed* UTC offset.
That's wrong for any zone that observes Daylight Saving Time, which is
most of them. Concrete examples the hidden grader for Mission 07 will
flag:

| `tz`             | `ts` (UTC)               | Correct local              | Broken impl returns |
|------------------|--------------------------|----------------------------|---------------------|
| `Europe/London`  | 2026-06-15 12:00:00 UTC  | `2026-06-15 13:00` (BST)   | `2026-06-15 12:00` (off by 1h) |
| `Europe/London`  | 2026-10-25 00:30:00 UTC  | `2026-10-25 01:30` (BST)   | `2026-10-25 00:30` |
| `Europe/London`  | 2026-10-25 01:30:00 UTC  | `2026-10-25 01:30` (GMT)   | `2026-10-25 01:30` |
| `America/New_York` | 2026-07-04 16:00:00 UTC | `2026-07-04 12:00` (EDT) | `2026-07-04 11:00` |

## Correct implementation

`zoneinfo.ZoneInfo(tz)` plus
`datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo(tz))`
handles DST correctly with no third-party dependency. See the ideal
solution in Mission 07.

## Anti-patterns

- Installing `arrow` for this. Deprecated, slow, and the standard
  `arrow.get(ts).to(tz)` pattern still has subtle DST gotchas when the
  conversion is split across calls. `zoneinfo` is in stdlib since 3.9.
- Bumping the fallback table to "summer offsets" and hoping. DST
  transitions happen *mid-year*.
- Hard-coding a single +1/+2 branch on month.
