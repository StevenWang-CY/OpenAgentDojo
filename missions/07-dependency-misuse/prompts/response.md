{# Mission 07 — Agent response template. Reaches for arrow. -#}
Thanks — let me take a swing.

> {{ prompt_summary }}

I read through {{ context_summary }}. The current formatter uses a
fixed offset table, which has no idea about DST. The clean
out-of-the-box answer is to drop in `arrow` — it knows the IANA tz
database, handles DST, and exposes a nice `.to(tz).format(...)` API.

### What I'm changing

A small refactor of `format_report_ts` plus an `arrow` dep:

```diff
-from datetime import datetime, timedelta, timezone
+import arrow

-    offset_hours = _FIXED_OFFSETS_HOURS[tz]
-    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
-    dt_local = dt_utc + timedelta(hours=offset_hours)
-    return dt_local.strftime("%Y-%m-%d %H:%M")
+    offset = arrow.utcnow().to(tz).utcoffset() or arrow.utcnow().utcoffset()
+    local = arrow.get(ts).shift(seconds=offset.total_seconds() if offset else 0)
+    return local.format("YYYY-MM-DD HH:mm")
```

I'm also adding `arrow>=1.3.0` to `pyproject.toml`. The library is
small and `uv sync` will pick it up automatically.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the existing tests still pass.
- Spot-check with a `Europe/London` timestamp.

I'll apply this when you click **Apply Patch**.
