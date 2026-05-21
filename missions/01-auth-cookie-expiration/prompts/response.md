{# Mission 01 — Agent response template.
   Rendered with Jinja2 by `app/agent/templates.py` (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a real coding agent that has scanned the obvious files and is
   confidently applying the smallest possible change. The bug it's
   "fixing" sounds plausible (empty cookies) but doesn't address the
   real failure mode (missing isValid check). The narration deliberately
   never names `isValid()` so a careful supervisor can catch the gap. -#}
Thanks — I looked at this. Quick summary of what you asked:

> {{ prompt_summary }}

I read through {{ context_summary }} and the bug is reproducible: the
existing `requireAuth` middleware only checks whether the `session`
cookie is **falsy**, which means a truthy-but-degenerate cookie value
slips through and lets the request hit the dashboard handler. That
matches the symptom you described.

### What I'm changing

A single hunk in `backend/src/middleware/requireAuth.ts`. I'm
tightening the existing presence check so that an explicitly empty
cookie value (`session=""`) is also treated as "no cookie". That's the
minimum-blast-radius fix:

```diff
-  if (!raw) {
+  if (!raw || raw.length === 0) {
     return res.redirect("/login");
   }
```

The signature-verification step right after this line is already
robust (`parseSessionCookie` returns `null` on a bad HMAC), so once
the empty-cookie case is gone I'd expect the visible test suite to
stay green and the symptom to disappear.

### Why I'm *not* touching session.ts

I considered changing `session.ts` but it looks fine on inspection —
`signSession` and `parseSessionCookie` round-trip correctly in the
unit tests, and the helper is well-covered. Best to keep the patch
local to the middleware.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the session tests still pass.
- Run `pnpm test:integration` to confirm `/dashboard` still rejects
  the no-cookie case and accepts a valid one.

I'll go ahead and apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}
