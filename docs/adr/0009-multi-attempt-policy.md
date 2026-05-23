# ADR 0009 — Multi-attempt scoring policy

- Status: Accepted
- Date: 2026-05-23
- Resolves: [OQ-0004](../open-questions.md#oq-0004--multi-attempt-scoring)
- Related: [ADR 0006 — scoring rubric](0006-scoring-rubric.md), [ADR 0010 — give-up policy](0010-give-up-policy.md)

## Context

Mission replay is part of the pedagogical loop — the platform actively
encourages "study the post-mortem, retry the mission" as a learning
mechanic. Until P0-3 there was no first-class affordance for replay, no
data model for ordering attempts, and no published rule for how a user's
public profile aggregated their multiple attempts on the same mission.

[OQ-0004](../open-questions.md) framed the open trade-off as
**best vs. latest vs. both**:

- *Best* rewards study-and-retry. Risks gaming: submit blanks, then
  replay with a cribbed solution.
- *Latest* rewards consistency. Risks penalising bold experimentation.
- *Both* (separate public/private aggregations) is the most honest but
  multiplies UI complexity and forces every consumer to read two numbers.

We also had to decide whether to **publicly expose attempt count** (e.g.
"attempted 47 missions") and whether **give-up attempts** (P0-4, ADR
0010) should be eligible for the public radar.

## Decision

1. **Public aggregates use best-per-mission.** The radar averages, the
   `total_missions` counter, and the `best_score` on
   `GET /profiles/{handle}` collapse multiple attempts on the same
   mission to a single representative attempt, selected by the policy
   below.

2. **Selection policy** (deterministic, mirrored in both
   `app.profiles.router._best_per_mission` and
   `app.missions.your_attempts.load_your_attempts`):

   1. Exclude grader-failure stubs (`score_report.is_stub == True`).
   2. Within the remaining attempts, prefer **uncapped over gave-up**:
      any attempt with `score_cap_reason IS NULL` wins over every
      attempt with `score_cap_reason = 'gave_up'`.
   3. Within the preferred tier, highest `total_score` wins; ties break
      to the most recent `completed_at` (then to row id for absolute
      determinism).

3. **Private surface — `your_attempts` on the mission detail page** —
   shows count, best, latest, and the signed delta from first → latest
   so the user can see their own trajectory. The strip is gated on
   authenticated callers; anonymous viewers see the standard
   "Start mission" CTA only.

4. **Attempt count is never public.** Public profiles do not surface the
   count of attempts on any mission. The private mission-detail strip
   does, but `GET /profiles/{handle}` does not — preventing "I attempted
   47 missions" grinding theatre.

5. **Gave-up attempts (ADR 0010) are excluded from the public radar
   when any uncapped attempt exists** on the same mission. A user
   whose only attempt was a give-up still has it on the radar (it's
   the best they've done); a user with a real 78 and a follow-up
   give-up of 50 shows the 78. This keeps the radar honest about
   peak supervision quality without rewarding give-ups as the path
   of least resistance.

6. **Audit trail.** `sessions.previous_session_id` links the new
   session to the prior one when created via the "Retry this mission"
   CTA. `ON DELETE SET NULL` so a P0-6 account hard-delete of an earlier
   attempt gracefully breaks the chain without raising FK errors.
   `sessions.attempt_index` is the 1-based ordinal of this attempt
   against `(user_id, mission_id)`, computed at create_session time.

## Consequences

- **Pedagogical loop is intact.** A user who studies the post-mortem
  and retries a mission with a higher score sees their public profile
  reflect the improvement. The first weaker attempt doesn't drag
  the radar down.
- **Grinding signal removed.** No public counter incentivises
  submitting empty attempts to inflate "tried 47 missions." The private
  surface keeps the count for the user's own trajectory but it never
  leaves the authenticated session.
- **Replay-determinism preserved.** The selection policy is a pure
  function of `(score, score_cap_reason, completed_at, id)`; a replay
  of the same DB state produces the same per-mission best.
- **Cost of giving up is calibrated.** A gave-up attempt only counts
  on the public profile when no real attempt exists; once the user
  retries and submits properly, the gave-up attempt is shadowed but
  not deleted — the audit trail still shows it.
- **Skills/mastery aggregation** (`GET /profiles/me/skills`) uses the
  same dedupe so a user with 3 attempts on the same mission counts
  once toward `sessions_attempted` and once toward `sessions_passed`
  (iff the best uncapped attempt cleared the threshold).
- **Multi-attempt UI cost** is bounded: one new strip on the mission
  detail page, one new button on the report footer. Both are
  conditionally rendered and degrade gracefully.

## What would change our mind

- Telemetry shows users repeatedly submitting empties to game the
  "best" selection. The defence is the rate-limit + the
  "your_attempts.count" the user sees on the private surface
  (visible self-judgement) — but if grinding emerges at scale, switch
  to **latest** for the public profile.
- A second cap reason emerges (forfeit / disqualification). The
  policy extends naturally — uncapped > gave-up > (next cap tier) —
  but ADRs and migrations will need updates.

## See also

- `apps/api/alembic/versions/0013_multi_attempt.py` — schema.
- `apps/api/app/profiles/router.py` (`_best_per_mission`) — selection.
- `apps/api/app/missions/your_attempts.py` — same policy on the
  mission detail page.
- `apps/web/components/catalog/YourAttemptsStrip.tsx` — private UI.
- `apps/web/components/report/ReportView.tsx` (`RetryMissionButton`) —
  the retry CTA.
