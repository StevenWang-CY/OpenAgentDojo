{# Mission 00 — Agent chain-of-thought template.
   Surfaced in the timeline as a collapsible "Agent reasoning" note.
   Tone: terse, internal-monologue, sounds responsible. Deliberately
   never asks "where does the name argument flow?" — the omission is the
   tutorial's teaching moment. -#}
[plan]
1. Read backend/src/utils/welcome.ts. The current return is a bare
   "Hello, !" string. Visible test asserts the trailing "!" — anything
   ending in "!" passes.
2. Defensive coding: input arguments should be sanitised before being
   trusted. Add `name.trim()` and branch on the trimmed length so an
   all-whitespace argument doesn't slip through as "valid".
3. Visible test continues to pass under the change.

[skipped]
- `backend/src/auth/session.ts`         — not related to greeting.
- `backend/src/middleware/requireAuth.ts` — not related to greeting.
- `backend/src/tests/unit/welcome.test.ts` — the only assertion is the
  trailing "!" character; the new diff keeps the same character there.

[risk]
Low. Single-line change in a util function with one caller in tests.
