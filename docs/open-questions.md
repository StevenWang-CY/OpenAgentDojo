# Open Questions

Track unresolved product/engineering decisions here. Each entry has a date opened, the question, the current best answer (if any), and a "resolve by" milestone. Resolve them before public beta.

## Conventions

- Add new questions at the **bottom**, with `## OQ-NNNN — <one-line title>` and the next monotonically increasing number.
- Mark resolved by changing the status and linking to the ADR or PR that settled it. Don't delete resolved questions — the history is useful.
- "Current best answer" is the leading hypothesis, not a commitment.

---

## OQ-0001 — Live partial credit during the mission

- Opened: 2026-05-21
- Status: open (M5 shipped with the current "process-signals-only" policy in place — keeping the OQ open for the user-research re-evaluation)
- Resolve by: public beta (post-launch user research)

**Question.** Should partial credit be revealed during the mission (live `ScorePreview`) or hidden entirely until submit?

**Current best answer.** Reveal *process* signals only. The `ScorePreview` panel shows "Context: 2/2 required selected", "Verification: tests not yet run", "Diff: 0 unrelated files changed" — never hidden-test outcomes, failure-mode hints, or a predicted total. See [IMPLEMENTATION_PLAN.md §13.5](../IMPLEMENTATION_PLAN.md). This is the shipped behaviour; the question stays open because we want to revisit it once we have real session telemetry from the beta.

**Why it matters.** Too much info turns the mission into a video game with a visible health bar. Too little leaves the user guessing whether their habits are landing. Process-only-signals is a defensible middle.

**What would change our mind.** User-research data showing the partial preview either (a) leads to gaming the rubric or (b) doesn't measurably reduce submission anxiety.

---

## OQ-0002 — "Give up" with ideal-solution reveal

- Opened: 2026-05-21
- Status: **resolved 2026-05-23** by [ADR 0010](./adr/0010-give-up-policy.md)
- Resolved at: P0-4 implementation (commit `7aac383`)

**Question.** Should we offer a "give up" action that lets the user see the ideal solution, capped at a score of 50 for the session?

**Current best answer.** Yes for shipped beta; soft-block for the first 10 minutes of the session to prevent quitting before engaging. The cap signals that giving up has a cost but the learning is still available.

**Why it matters.** Without it, frustrated users abandon and don't learn anything. With it, we have to be careful not to make "give up" the default path.

**What would change our mind.** Catalog-level data showing "give up" rate exceeds 25% — would indicate the missions are too hard, not that the affordance is misconfigured.

---

## OQ-0003 — Pricing model

- Opened: 2026-05-21
- Status: open
- Resolve by: post-MVP

**Question.** Free with rate limits and a paid tier for unlimited replays, or fully free during early access?

**Current best answer.** Fully free during MVP and beta (first 90 days post-launch). Introduce a paid tier with unlimited replays + team analytics after we have ≥1000 graded submissions of usage data to inform pricing.

**Why it matters.** Pricing impacts marketing copy on the landing page (free vs trial messaging) and the auth flow (need payment integration for paid).

**What would change our mind.** Strong demand from teams pre-launch (e.g. enterprise interest in a workshop offering) might justify monetizing earlier.

---

## OQ-0004 — Multi-attempt scoring

- Opened: 2026-05-21
- Status: **resolved 2026-05-23** by [ADR 0009](./adr/0009-multi-attempt-policy.md)
- Resolved at: P0-3 implementation (commit `7aac383`)

**Question.** When a user replays a mission, what do we show on the public profile and in radar averages: best score, latest score, or both?

**Current best answer.** Show **best** in the public profile (encourages improvement), and the **delta** between first and latest attempt in the user's private dashboard (rewards improvement loops). Hide the count of attempts publicly.

**Why it matters.** Shapes how users approach replays. "Best score" encourages study-and-retry; "latest score" rewards consistency; "both" risks cluttering the profile.

**What would change our mind.** If we see users gaming "best" by submitting empties first and replaying with cribbed answers, we'd switch to "latest".

---

## Resolved

- **OQ-0002** ("Give up" with ideal-solution reveal) — resolved 2026-05-23
  by [ADR 0010](./adr/0010-give-up-policy.md). Score cap 50/100, 10-min
  soft block, no hiding from the public profile; dimension scores remain
  honest. Skills/radar aggregation excludes gave-up attempts when any
  uncapped attempt exists.
- **OQ-0004** (Multi-attempt scoring) — resolved 2026-05-23 by
  [ADR 0009](./adr/0009-multi-attempt-policy.md). Public aggregates use
  best-per-mission (uncapped beats gave-up within tier); private mission
  detail page surfaces count + best + latest + delta. Attempt count is
  never public.

## References

- [IMPLEMENTATION_PLAN.md §27](../IMPLEMENTATION_PLAN.md)
- [docs/adr/README.md](./adr/README.md) — when an OQ is resolved, write an ADR if the decision is load-bearing
