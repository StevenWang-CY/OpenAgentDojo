# ADR 0010 — Give-up affordance with capped ideal-solution reveal

- Status: Accepted
- Date: 2026-05-23
- Resolves: [OQ-0002](../open-questions.md#oq-0002--give-up-with-ideal-solution-reveal)
- Related: [ADR 0006 — scoring rubric](0006-scoring-rubric.md), [ADR 0009 — multi-attempt policy](0009-multi-attempt-policy.md)

## Context

The product positions itself as **training**, not assessment. A frustrated
user who abandons mid-mission learns nothing — the ideal solution, the
post-mortem walkthrough, and the dimension breakdown all live behind the
submit gate. Without a deliberate forfeit affordance, the only paths out
of a stuck mission are:

- Force-submit a broken attempt (collapses the rubric to 0/100 and the
  user closes the tab without reading the post-mortem).
- Abandon the workspace (session goes to `abandoned`; report is never
  generated; nothing learned).

Both are worse outcomes than "give the user the answer in exchange for a
known cap." [OQ-0002](../open-questions.md) framed the trade-off as
**reveal-with-cost vs. no-reveal-at-all**:

- *No reveal* preserves rubric purity but trains nothing for stuck users.
- *Reveal with no cost* destroys the rubric (every score is now a max).
- *Reveal with a hard cap* is the defensible middle.

Two adjacent decisions:
- The **minimum-time gate** before "Give up" becomes available
  (prevents quitting before engaging).
- Whether the cap mutates dimension scores (collapses honesty) or only
  the total (preserves honest signal).

## Decision

1. **Hard cap of 50/100** on the resulting submission when the user
   invokes the give-up affordance. Stored as
   `submissions.score_cap_reason = 'gave_up'`; the cap is applied AFTER
   all dimensions are scored, so the breakdown remains honest. The FE
   renders a chip in the report header explaining the cap, with the
   uncapped (would-have-scored) value beside it.

2. **10-minute soft block** before the affordance is available. The
   server gate (425 Too Early) is authoritative; the FE disables the
   button and renders a countdown for ergonomics. Documented in
   `GIVE_UP_MIN_SECONDS` (10 minutes) — kept in seconds so it can be
   overridden per-mission later without changing units.

3. **The cap touches `total`, not dimensions.** Each dimension scorer
   produces its honest 0..N score; `apply_score_cap` records the
   uncapped total in `score_report.uncapped_total`, then lowers
   `total` to `min(total, 50)`. A user who gave up at minute 11 on
   a strong attempt (e.g. 38/100 honest) sees their real score
   AND the chip — the cap was not binding, but the deliberate forfeit
   is still visible to the user (and to the profile aggregator).

4. **No hiding from the profile.** The submission still lands on the
   user's `MissionHistoryTable`. The radar averages and best-per-mission
   selection (ADR 0009) prefer uncapped attempts when any exist — so
   a strong real attempt isn't shadowed by a 50-cap, but a gave-up-only
   user does see their gave-up score on the profile.

5. **Honest timeline.** Give-up emits a `session.gave_up` supervision
   event with `seconds_into_session` BEFORE the column write. The
   replay artifact (event log) shows the deliberate forfeit; the
   post-mortem walkthrough can reference it.

6. **Workspace state transitions are unchanged.** The give-up endpoint
   hands off to `submit_session` immediately, so the session moves
   through the standard `active → submitting → graded` cycle. No new
   terminal state, no new sandbox cleanup path.

## Consequences

- **Stuck users learn something.** The ideal-solution markdown +
  three-way diff + post-mortem walkthrough surface on the report page
  regardless of cap status, because the submit pipeline runs to
  completion. ADR 0010 makes this affordable.
- **Rubric purity preserved.** Dimension scores are untouched. A
  give-up at minute 11 with strong context-selection + verification
  still shows those scores honestly — the cap only collapses the
  rolled-up total.
- **Cost is calibrated.** 50 is the midpoint of the rubric: a strong
  attempt would beat it; a weak attempt is no worse than what a
  rushed submit would have produced. Either way, the cap teaches
  "engagement has a real cost — don't quit casually."
- **Skills/mastery aggregation** (`GET /profiles/me/skills`) and the
  radar (ADR 0009) exclude gave-up attempts when any uncapped attempt
  exists. A power user who gives up once and aces the retry doesn't
  pay twice.
- **10-min gate is global**. Future per-mission overrides
  (e.g. `mission.give_up_after_seconds: 1200` on advanced missions)
  are a schema-additive change — the gate constant becomes a manifest
  field, the endpoint reads it from the loaded manifest.
- **The give-up endpoint is `POST` + CSRF-protected** (default policy
  in `docs/security.md`). Mid-flight submit + give-up are reconciled
  via the same atomic `active → submitting` claim the existing submit
  flow uses, so a double-submit race is impossible.

## What would change our mind

- Catalog-level data showing give-up rate > 25% (per
  [OQ-0002](../open-questions.md)). That signals missions are too
  hard, not that the affordance is misconfigured. The fix is mission
  tuning, not removing the affordance.
- Telemetry shows users invoking give-up immediately at minute 10:00
  on most missions. Either bump the gate to 15 min globally or add
  the per-mission override sooner than planned.
- A failure case where dimension scores are weirdly inflated for a
  give-up attempt (e.g. a context-selection score of 10/10 for a
  user who only selected files to reveal the answer). The current
  rubric uses event-grounded signals, so this is unlikely — but
  worth instrumenting on roll-out.

## See also

- `apps/api/alembic/versions/0014_give_up.py` — `sessions.gave_up_at`.
- `apps/api/alembic/versions/0013_multi_attempt.py` — shared
  `submissions.score_cap_reason` column.
- `apps/api/app/grading/score.py` (`apply_score_cap`,
  `GAVE_UP_SCORE_CAP`) — pure cap application.
- `apps/api/app/grading/runner.py` (post-`compute_score`) — wires
  the cap into the pipeline.
- `apps/api/app/sessions/router.py` (`post_give_up`,
  `GIVE_UP_MIN_SECONDS`) — endpoint + gate.
- `apps/web/components/workspace/GiveUpDialog.tsx` — FE dialog.
- `apps/web/components/report/ReportView.tsx` — chip in report header.
