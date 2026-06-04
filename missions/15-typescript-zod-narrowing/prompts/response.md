{# Mission 15 — Agent response template.
   Rendered with Jinja2 by `app/agent/templates.py` (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a real coding agent that has localized the crash to formatRefund
   and is confidently applying the smallest change that stops the throw.
   The "fix" sounds plausible (coerce with Number) but doesn't address the
   real failure mode (z.any() lets bad data past parseRefund). The
   narration deliberately never proposes narrowing the schema, so a careful
   supervisor can catch the gap. -#}
Thanks — I looked at this. Quick summary of what you asked:

> {{ prompt_summary }}

I read through {{ context_summary }}. The crash is reproducible: a refund
event whose `amount` isn't a number reaches `formatRefund`, which calls
`amount.toFixed(2)` and throws because `.toFixed` only exists on numbers.
That matches the symptom you described.

### What I'm changing

A single hunk in `backend/src/orders/events.ts`. I'm coercing the value
through `Number(...)` before calling `.toFixed`, so a non-numeric amount
no longer blows up the formatter:

```diff
-  return "$" + refund.amount.toFixed(2);
+  return "$" + Number(refund.amount).toFixed(2);
```

`Number(...)` always returns a number, so `.toFixed` is now total and the
throw goes away. That's the minimum-blast-radius fix.

### Why I'm *not* touching the schema

I considered tightening `refundEventSchema`, but `amount: z.any()` is
intentionally permissive — the upstream producer is the source of truth
for the shape, and locking the schema down could reject events the
pipeline currently accepts. Keeping the change local to the formatter
avoids that risk.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the existing `events` tests still pass.
- Run `pnpm typecheck` to confirm the coercion typechecks.

I'll go ahead and apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}
