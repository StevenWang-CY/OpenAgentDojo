# OpenAgentDojo — P0 Implementation Design

Companion to [FEATURE_GAPS.md](FEATURE_GAPS.md). Covers P0-1 through P0-6 in
implementable detail: data model, API contract, frontend surface, scoring
interactions, edge cases, tests, and rollout order.

Each section ties its choices back to the platform's stated goal — *training
the eye that catches supervisor failure* — and to the load-bearing
architectural invariants (event-sourced supervision, deterministic grading,
mission manifests as content contract).

---

## 0. Cross-cutting decisions

These show up in every P0 below; resolved here once so the per-item sections
can stay tight.

### 0.1 Migration order

The six P0 items add four migrations. They are independent, so they can ship
in any order — but the file numbers are reserved up-front to avoid
collisions on parallel branches:

| Migration | File | P0 item | Adds |
|---|---|---|---|
| 0011 | `0011_tutorial_progress.py` | P0-1 | `users.tutorial_completed_at`, `missions.kind` |
| 0012 | `0012_post_mortem_evidence.py` | P0-2 | `submissions.critical_moments` (JSONB), strengths/weaknesses become evidence-bearing JSONB |
| 0013 | `0013_multi_attempt.py` | P0-3 | `sessions.attempt_index`, `submissions.score_cap_reason` (also used by P0-4) |
| 0014 | `0014_give_up.py` | P0-4 | `sessions.gave_up_at` |
| 0015 | `0015_user_consents.py` | P0-5 | `user_consents` table, `users.pending_consent_version` |
| 0016 | `0016_account_self_service.py` | P0-6 | `users.pending_email`, `users.deletion_scheduled_at`, `data_exports` table |

If P0-3 and P0-4 ship in the opposite order, swap 0013/0014 — `score_cap_reason`
should be added by whichever lands first.

### 0.2 Shared types & schema regen

Every P0 changes the OpenAPI surface. The shared-types pipeline already runs
on the API container — every PR that touches `app/schemas/*` or
`app/*/router.py` must:

1. Update the Pydantic model.
2. Run `pnpm --filter @arena/api openapi:dump` (or whatever the existing
   CI step is — see `packages/shared-types/`).
3. Regenerate `packages/shared-types/src/api.ts` so the FE compiles.

This is enforced by the existing typecheck CI gate; no new tooling needed.

### 0.3 Event-sourcing invariant

The supervision-event log is append-only and replay-deterministic. Every new
user action introduced by a P0 emits a typed event so the grader, the
post-mortem walkthrough (P0-2), and any future replay tool see the same
data. New event types added below:

| Event type | Emitted by | Payload shape |
|---|---|---|
| `tutorial.step_completed` | P0-1 | `{step_id: string, mission_id: "orientation"}` |
| `tutorial.dismissed` | P0-1 | `{step_id: string}` |
| `session.reset` | P0-12 (note: in FEATURE_GAPS, not redesigned here) | `{committed_files_discarded: int}` |
| `session.gave_up` | P0-4 | `{seconds_into_session: int}` |
| `consent.granted` | P0-5 | `{kind: 'analytics'\|'functional', version: int}` |
| `consent.revoked` | P0-5 | `{kind: 'analytics'\|'functional'}` |
| `account.email_change_requested` | P0-6 | `{new_email_hash: string}` |
| `account.deletion_scheduled` | P0-6 | `{scheduled_for: iso8601}` |
| `account.deletion_cancelled` | P0-6 | `{}` |

`account.*` events live on a sentinel session row keyed by user (or a new
`account_events` table). See P0-6 for the choice.

### 0.4 Scoring policy table

| Event | Effect on this submission's `score_report` |
|---|---|
| Tutorial mission completed | `score_report = null`, `tutorial_completed_at` set on `users` |
| `session.gave_up` | `total = min(total, 50)`, `score_cap_reason = "gave_up"` |
| `session.reset` | Each reset emits a `−1 Safety` deduction signal (configurable per mission) |

The hidden-test cap (Final Correctness ≤ 18) and the Agent-Review 0/15 floor
remain untouched. The give-up cap is applied **after** the dimension scores
are computed, at the report-total stage — so the dimension scores remain
honest and the cap shows up as a single `score_cap_reason` field rather than
silently mutating individual dimensions.

---

## P0-1. In-product onboarding ("Mission 00")

### Goal

A first-time, never-signed-in visitor can land → sign up → finish a guided
orientation session → understand the dojo's eight workspace surfaces and the
six supervision behaviours that the grader actually measures, in **under 8
minutes**. Subsequent missions never need to teach UI affordances again.

The orientation is implemented as a real Mission (`missions/00-orientation/`)
plus a thin coachmark overlay that runs in tutorial mode. Reusing the
mission machinery is the cheapest path to "tutorial behaves like the real
thing, because it *is* the real thing."

### Architecture

```
[ apps/web/components/tutorial/ ]
    TutorialController.tsx     – orchestrates step sequence; reads/writes
                                  tutorial state on the user; subscribes to
                                  supervision events to auto-advance
    Coachmark.tsx              – single popover anchored to a DOM ref
    tutorial-steps.ts          – ordered step definitions (id, anchor,
                                  copy, advanceCondition)

[ apps/api/app/missions/manifest.py ]
    + kind: Literal["standard", "tutorial"] = "standard"

[ apps/api/app/grading/runner.py ]
    if mission.kind == "tutorial": short-circuit to tutorial-completion
    branch (skips scoring, badges, score_report persistence; just marks
    users.tutorial_completed_at = now()).

[ missions/00-orientation/ ]
    mission.yaml               – kind: tutorial; trivially-easy bug
    agent_patch.diff           – patch that mis-edits the wrong line
    ideal_solution.md / .diff  – the intended fix
    hidden_tests/              – one test (so the visible/hidden split
                                  itself is part of the tutorial)
```

### Data model (migration 0011)

```sql
ALTER TABLE missions
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'standard'
        CHECK (kind IN ('standard', 'tutorial'));

ALTER TABLE users
    ADD COLUMN tutorial_completed_at TIMESTAMPTZ NULL,
    ADD COLUMN tutorial_replay_count INTEGER NOT NULL DEFAULT 0;
```

`tutorial_replay_count` is the audit trail for "user replayed tutorial 3
times" — useful telemetry for content tuning, never shown publicly.

### Manifest schema delta

[`apps/api/app/missions/manifest.py`](apps/api/app/missions/manifest.py)
gains a `kind` field on `MissionManifest`. The JSON Schema at
[`docs/schemas/mission.schema.json`](docs/schemas/mission.schema.json) is
extended:

```json
"kind": { "enum": ["standard", "tutorial"], "default": "standard" }
```

Tutorial missions are **exempt** from the `pnpm validate:missions`
`acceptance.yaml` envelopes (they don't have a score), but must still:

- Apply `agent_patch.diff` cleanly.
- Pass all visible tests after applying `ideal_solution.diff`.
- Set `expected_context.required` to at least 2 files (so the
  "select context" step has something to teach against).

### API surface

No new endpoints. Two existing endpoints adapt:

- `POST /api/v1/sessions` accepts `mission_id: "orientation"`. The
  provisioning path is identical; the only difference is the runner
  short-circuit at submit time.
- `GET /api/v1/me` extends `User` response with `tutorial_completed_at`
  so the FE can decide whether to render the "Start here" banner.

One new internal event:

- `tutorial.step_completed` — emitted by the FE via the existing
  supervision-event channel (`POST /api/v1/sessions/{id}/events/...`
  extended with a `/tutorial-step` endpoint). The grader ignores it for
  scoring; the post-mortem (P0-2) does not consume it.

### Frontend surface

#### Catalog "Start here" affordance

[`apps/web/components/marketing/ScenarioCarousel.tsx`](apps/web/components/marketing/ScenarioCarousel.tsx)
and [`apps/web/components/catalog/MissionGrid.tsx`](apps/web/components/catalog/MissionGrid.tsx)
gain a top-row banner that renders when `meQuery.data?.tutorial_completed_at`
is null:

```
┌──────────────────────────────────────────────────────────┐
│ // start here                                            │
│ 00 · Orientation — learn the dojo in ~8 minutes      →   │
└──────────────────────────────────────────────────────────┘
```

Already-completed users see the row collapsed to a single muted line:
"// orientation · completed 2026-04-12 · replay →".

#### Coachmark sequence

Six steps, anchored to existing DOM elements via React refs already in the
workspace shell:

| # | Anchor | Copy (short) | Advance condition |
|---|---|---|---|
| 1 | FileTree | "Tick the files the agent should read. Context selection is scored." | First `context.selected` event with ≥1 file |
| 2 | AgentChat composer | "Write a prompt that names the bug and asks for a regression test." | First `prompt.submitted` ≥ 40 chars |
| 3 | "Apply patch" button | "Read the agent's narration before applying." | `patch.applied` event |
| 4 | Diff tab | "Open the diff. Did the agent change what you asked for?" | `diff.opened` event |
| 5 | Terminal / Tests panel | "Run the tests. Verification is the highest-leverage habit." | First `command.run` with category `test` or `typecheck` |
| 6 | Submit button | "Submit. The next page shows what you did vs. what was expected." | `submission.requested` event |

`TutorialController` subscribes to the same WS supervision-event stream the
Timeline already reads. Each step is a pure function of `(events, currentStep)`
— no extra polling. Steps can be dismissed (`tutorial.dismissed` event) but
the run continues; the *only* way to mark the tutorial complete is to actually
submit Mission 00.

#### "Replay tutorial" entry

A new item in the existing
[Header.tsx](apps/web/components/layout/Header.tsx) user dropdown:

```
@handle
─────────────────────
Profile
Skills
Account              (P0-6)
Replay tutorial      (new)
─────────────────────
Sign out
```

Hitting "Replay tutorial" sets `users.tutorial_completed_at = NULL` and
`tutorial_replay_count += 1`, then redirects to
`/missions/orientation` (which immediately auto-creates a new session).

### Edge cases

- **User dismisses every step then submits.** Submit still works; tutorial
  is marked complete; the catalog banner disappears. The dismissals are in
  the event log for content tuning.
- **User completes Mission 00 multiple times before any other.** Catalog
  shows "completed" after the first; replay does not reset the catalog
  banner.
- **Tutorial mission's hidden test fails on submit.** Treated as
  "completion succeeded" anyway — tutorial mission grading is by completion,
  not by passing.
- **Concurrent session conflict.** If the user already has an active
  non-tutorial session, "Start here" must surface the existing
  `active_session_exists` 409 message (already implemented in
  [sessions/router.py:164](apps/api/app/sessions/router.py#L164)); a banner
  in the catalog says "finish your current mission first, then come back."

### Testing

- Pytest: `test_tutorial_runner_short_circuit` — submitting `kind=tutorial`
  mission persists no `submissions` row but sets `users.tutorial_completed_at`.
- Vitest: `tutorial-controller.test.tsx` — given an event stream, asserts
  step transitions.
- Playwright: full landing → sign up → tutorial → catalog without banner
  flow in `apps/web/e2e/tutorial.spec.ts`.
- Mission self-test: `missions/00-orientation/acceptance.yaml` carries
  only `applies_cleanly: true` and `ideal_solution_passes_all_tests: true`.

### Rollout

Single PR. No dependency on other P0s. The new event types ride existing
WS infra. Migration 0011 is forward-only safe (defaulted columns).

### Open decisions

- **Should the tutorial be skippable from the catalog?** Recommendation:
  yes (a "Skip orientation" link under the banner), with the dismissal
  event recorded. Forcing the tutorial annoys returning power users from
  other tools who already understand IDE-shaped products.

---

## P0-2. Post-mortem walkthrough

### Goal

When the user finishes a mission — passed or not — the report contains a
**training surface**, not a measurement readout. The user can see, in one
view:

1. The user's final diff vs. the ideal solution diff vs. the agent's
   original (deliberately-flawed) patch, rendered as a three-layer
   side-by-side diff with the load-bearing lines highlighted.
2. The "critical moment" on the timeline: the supervision event after which
   the user committed to the wrong path. Hovering scrubs through the
   surrounding events.
3. Every strength and weakness in the score report cites the specific
   supervision events it references; clicking a strength scrolls the
   timeline to those events.

This converts the report from "here's your grade" to "here's where you
went wrong and how to spot it next time."

### Architecture

```
[ missions/<id>/ ]
    + ideal_solution.diff      ← NEW REQUIRED FILE for non-tutorial missions

[ apps/api/app/grading/score.py ]
    ScoreReport.strengths  : list[StrengthEntry]    (was list[str])
    ScoreReport.weaknesses : list[WeaknessEntry]    (was list[str])
    ScoreReport.critical_moments : list[CriticalMoment]
    – each entry carries evidence_event_ids: list[int]

[ apps/api/app/grading/diagnostics.py ]
    + compute_critical_moments(events, mission) → list[CriticalMoment]
    Heuristic, fully deterministic — see "Critical-moment heuristic" below.

[ apps/web/components/report/ ]
    PostMortemWalkthrough.tsx       – the section assembling the three pieces
    ThreeWayDiff.tsx                – diff layers: user-final vs ideal vs agent
    CriticalMomentScrubber.tsx      – timeline scrubber pinned to one moment
    DimensionEvidence.tsx           – strength/weakness items with event links
```

### Data model (migration 0012)

The persisted `submissions.score_report` JSONB grows. Existing rows continue
to render (older `strengths: list[str]` are upcast on read by the FE to
`{message, evidence_event_ids: []}`). New writes use the richer shape. No
column type changes — JSONB.

The schema at
[`docs/schemas/score_report.schema.json`](docs/schemas/score_report.schema.json)
adds:

```json
{
  "strengths": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["message", "evidence_event_ids", "dimension"],
      "properties": {
        "message": { "type": "string" },
        "evidence_event_ids": { "type": "array", "items": { "type": "integer" } },
        "dimension": { "enum": ["final_correctness","verification","agent_review",
                                "prompt_quality","context_selection","safety",
                                "diff_minimality"] }
      }
    }
  },
  "weaknesses": { /* same shape */ },
  "critical_moments": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["event_id", "kind", "explanation"],
      "properties": {
        "event_id": { "type": "integer" },
        "kind": { "enum": ["agent_responded_no_review",
                            "submitted_without_verification",
                            "wrong_layer_committed",
                            "missed_corrective_window"] },
        "explanation": { "type": "string" },
        "what_to_do_instead": { "type": "string" }
      }
    }
  }
}
```

The legacy `feedback_narrative` field stays — it remains the
dimension-level prose. `critical_moments` is the *event-level*
prose. They complement each other.

### Manifest schema delta

Every non-tutorial mission must now ship `ideal_solution.diff` *in addition
to* `ideal_solution.md`. The validator (`scripts/validate_missions.py`)
adds:

- File exists.
- Applies cleanly on `initial_commit` (uses the same `git apply --check`
  the runner uses for `agent_patch.diff`).
- Applying it then running the full visible+hidden test suite passes
  100%.

Existing missions get a one-time backfill PR that hand-converts each
`ideal_solution.md`'s embedded diff fences into a `.diff` file.

### Critical-moment heuristic (deterministic)

Implemented in
`apps/api/app/grading/diagnostics.py:compute_critical_moments`. Pure
function over the event stream:

1. **`agent_responded_no_review`** — emitted when there's an
   `agent.responded` event whose latest-following `patch.applied` is
   *not* followed by any `diff.opened` before `submission.requested`. The
   linked event is the `agent.responded`.

2. **`submitted_without_verification`** — emitted when the entire event
   stream contains zero `command.run` events with category in
   `{test, typecheck}` *and* the user submitted. Linked event is the
   first `prompt.submitted`.

3. **`wrong_layer_committed`** — emitted when the `forbidden_changes`
   validator triggered AND `file.reverted` does not appear on the
   forbidden path. Linked event is the `patch.applied`.

4. **`missed_corrective_window`** — emitted when `submission.requested`
   occurs within 15 seconds of the last `agent.responded`. Linked event
   is the `agent.responded`.

Each kind ships in a deterministic order. At most 3 moments surface per
report (rank by severity: scoring impact). The function is unit-tested
against fixture event streams the same way the dimension scorers are.

### API surface

The existing `GET /api/v1/reports/{submission_id}` endpoint already returns
`score_report` and `ideal_solution` (markdown). It gains a sibling field:

```typescript
type SubmissionRead = {
  // ... existing fields
  ideal_solution: string | null;          // markdown (unchanged)
  ideal_solution_diff: string | null;     // unified diff (new — loaded
                                          //   from missions/<id>/ideal_solution.diff
                                          //   only post-grading)
  agent_patch_diff: string | null;        // unified diff (new — for the
                                          //   three-way view)
};
```

Both `ideal_solution_diff` and `agent_patch_diff` are gated on
`session.status == "graded"` (same gating that the current
`ideal_solution` field uses; see
[reports/router.py:163](apps/api/app/reports/router.py#L163)).

### Frontend surface

The existing
[`ReportView`](apps/web/components/report/ReportView.tsx) is restructured:

```
┌────────────────────────────────────────────────────────────────┐
│ Score: 70 / 100                       Failure mode: identified │
│ submission · 7c41…                                  [share][⎙] │
├────────────────────────────────────────────────────────────────┤
│ // what you take away                                          │
│                                                                │
│ Critical moment — 00:03:42                                     │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ agent.responded · proposed presence-only check.          │   │
│ │ You submitted 11 seconds later without opening the diff. │   │
│ │ What to do instead: open the diff and search for the     │   │
│ │ word "expiration" before pressing Apply.                 │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                │
│ ◀── 00:03:30 ──── 00:03:42 ────────────────── 00:03:53 ──▶     │
│   prompt.submitted   agent.responded         submission.req    │
├────────────────────────────────────────────────────────────────┤
│ // what the agent did vs. what was expected                    │
│  ┌──────────── you submitted ────────┬── ideal solution ─────┐ │
│  │ + raw.length === 0                 │ + if (!session ||      │ │
│  │   return raw["uid"] …              │      !session.isValid │ │
│  │                                    │       (Date.now()))   │ │
│  │                                    │   return res.redirect │ │
│  │                                    │     ("/login");       │ │
│  └────────────────────────────────────┴───────────────────────┘ │
│                                                                │
│ // performance overview · strengths · weaknesses               │
│  Verification discipline  13/15                                │
│   ✓ ran auth-focused tests           → events #34, #35         │
│   ✕ did not run typecheck            → (no events)             │
├────────────────────────────────────────────────────────────────┤
│ // ideal solution narrative                                    │
│  <markdown of ideal_solution.md, unchanged>                    │
└────────────────────────────────────────────────────────────────┘
```

The three-way diff renders the *user's diff vs initial commit* on the left
and the *ideal solution vs initial commit* on the right. The agent's
original patch is folded in as a "show what the agent first proposed"
expandable strip beneath (not always in view; it's the third layer, less
load-bearing). Both sides use the existing
[`DiffViewer`](apps/web/components/workspace/DiffViewer.tsx) in
side-by-side mode.

Each evidence link is a button that:

1. Highlights the referenced event(s) in the
   [`TimelineReplay`](apps/web/components/report/TimelineReplay.tsx)
   component below.
2. Scrolls the timeline to that event with a 1.2s pulse animation.

### Scoring interactions

The score engine in
[`apps/api/app/grading/score.py`](apps/api/app/grading/score.py) already
emits strengths and weaknesses keyed off specific events (e.g. "ran
auth-focused tests" comes from a `command.run` event with category
`test`). The change is plumbing — every place that today appends a string
to `strengths` or `weaknesses` must also pass the event id(s) it consumed.

Concretely, the helpers `_events_of_type` and `_event_occurred_before` in
score.py currently throw away the matched event. They're updated to
return `(matched, event_ids)` tuples and every caller gets the event ids
into the resulting strength/weakness entry. The new `dimension` field is
the dimension being scored at that callsite — already known.

### Edge cases

- **Submission failed before grading.** `ideal_solution_diff` and
  `agent_patch_diff` are still null; the report renders the existing
  "submission failed before grading completed" state.
- **Legacy submissions (pre-migration).** Their stored `strengths` /
  `weaknesses` are strings. The FE handles the union: `string |
  StrengthEntry`. Treated as `{message: s, evidence_event_ids: [],
  dimension: 'unknown'}`. No backfill needed; old reports just look
  un-clickable.
- **No supervision events found for a signal.** Rare but possible (the
  signal came from a validator, not an event). `evidence_event_ids: []`
  and the FE omits the "→ events #N" affordance.
- **Mission shipped without ideal_solution.diff.** Validator blocks
  merge; existing missions get a one-PR backfill before this ships.
- **The "critical moment" heuristic finds zero moments.** The section
  renders only the diff comparison, not the scrubber. Always graceful.

### Testing

- Pytest `test_critical_moments_*.py` (one fixture event stream per
  `kind`, asserts the linked event id).
- Pytest `test_score_evidence_links.py` — every strength/weakness emitted
  by `compute_score` against a canonical event stream has
  `evidence_event_ids` set (none empty) for the dimensions where evidence
  is expected.
- Vitest `post-mortem-walkthrough.test.tsx` — given a fixture
  ScoreReport with strengths/weaknesses pointing at event ids, clicking
  an evidence link calls `onScrollToEvent(id)`.
- Playwright `report-walkthrough.spec.ts` — full flow from a
  pre-seeded submission to a click on a strength → timeline scroll.

### Rollout

Two-step. Step 1 ships the schema, the runner change, and the
`ideal_solution.diff` backfill for all 10 missions (one PR, ~10 file
changes). Step 2 ships the frontend. The new fields are tolerant of
absence so the FE PR is unblocked by the BE PR by a few days.

### Open decisions

- **Should the critical-moment heuristic be LLM-augmented for prose?**
  Today the `explanation` and `what_to_do_instead` strings are
  templated per `kind`. Plausible upgrade: pass them through the
  existing prompt-judgement infrastructure for natural-language polish.
  Out of scope for P0 because (a) it adds an LLM dependency to the
  report-render path, (b) the templates work. Note for later.
- **Three-way vs two-way diff.** I designed three-way; two-way (user vs
  ideal) is the minimum viable. Three-way is more honest because it
  shows the user's *delta against the agent's wrong patch*, not the
  initial commit. Worth keeping unless ergonomics push back during
  design review.

---

## P0-3. Multi-attempt scoring policy

### Goal

The product encourages *deliberate replay* as a learning loop. The user
can attempt a mission as many times as they want; the public profile
reflects their **best** score per mission; the user's own mission detail
page shows best, latest, and the delta so they can see improvement.
Attempt counts are never shown publicly (to prevent grinding signal).
[OQ-0004](docs/open-questions.md) is closed by an ADR.

### Architecture

```
[ apps/api/alembic/versions/0013_multi_attempt.py ]
    + sessions.attempt_index INTEGER NOT NULL DEFAULT 1
    + sessions.previous_session_id UUID NULL REFERENCES sessions(id)
        (set when the new session is a deliberate "Retry mission" click)
    + submissions.score_cap_reason TEXT NULL CHECK IN ('gave_up')
        (also used by P0-4; whichever migration lands first owns this column)

[ apps/api/app/sessions/service.py ]
    create_session(...) ← computes attempt_index from prior count for the
                          same (user_id, mission_id) where status='graded'

[ apps/api/app/profiles/router.py ]
    _fetch_stats(...) ← per-mission MAX(score) aggregation; total_missions
                        becomes the count of DISTINCT mission_ids with at
                        least one graded session

[ apps/api/app/missions/router.py ]
    GET /missions/{id} ← extended for signed-in users with `your_attempts`
                          summary
```

### Data model (migration 0013)

```sql
ALTER TABLE sessions
    ADD COLUMN attempt_index INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN previous_session_id UUID NULL REFERENCES sessions(id);

ALTER TABLE submissions
    ADD COLUMN score_cap_reason TEXT NULL
        CHECK (score_cap_reason IS NULL OR score_cap_reason IN ('gave_up'));

-- Backfill existing sessions: attempt_index = row_number over
-- (user_id, mission_id, completed_at).
WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY user_id, mission_id
        ORDER BY completed_at NULLS LAST, started_at
    ) AS n FROM sessions WHERE status = 'graded'
)
UPDATE sessions s
SET attempt_index = numbered.n
FROM numbered
WHERE s.id = numbered.id;
```

### API surface

**`MissionDetail` schema extension** (signed-in callers only):

```typescript
type YourAttempts = {
  count: number;            // total graded attempts on this mission
  best_score: number | null;
  best_submission_id: string | null;
  latest_score: number | null;
  latest_submission_id: string | null;
  delta: number | null;     // latest - first; null if count < 2
};

type MissionDetail = {
  // ... existing fields
  your_attempts: YourAttempts | null;   // null for anonymous viewers
};
```

The endpoint signature does not change; the body shape does. Anonymous
callers continue to see `your_attempts: null` and behave as today.

**Profile aggregation contract change.** The `PublicProfile.radar_averages`
and `PublicProfile.best_score` now reflect *best per mission*, not the
average across every submission. This is a *user-visible* change in number,
so the ADR (below) documents it as the explicit policy.

The `history` array continues to show every graded session — that's the
private surface (and the public `/profile/{handle}` page already truncates
to 25 entries). Attempt count is only shown on the private mission detail
page, never on the public profile.

### Frontend surface

**Mission detail page** ([MissionDetailView.tsx](apps/web/components/catalog/MissionDetailView.tsx)):

For signed-in users, a strip above the "Start mission" CTA:

```
// your attempts
3× attempted · best 78 · last 72 · ▲ +6 vs first attempt
```

Hovering the "▲ +6" reveals a tiny sparkline of all attempts.

**Report page** ([ReportView.tsx](apps/web/components/report/ReportView.tsx)):

The bottom CTA row becomes:

```
[ ← Back to missions ]      [ ↻ Retry this mission ]  [ Next mission → ]
```

The retry button is wired to
`POST /api/v1/sessions` with `{mission_id, previous_session_id: <submission.session_id>}`.
The new session inherits `previous_session_id` so the audit trail links
attempts together.

**Profile page** is unchanged. The public `MissionHistoryTable` was already
de-facto showing one row per submission, which surfaces multi-attempts
honestly without leaking a "count" anywhere.

### Scoring interactions

The radar averages and `total_missions` aggregation in
[profiles/router.py](apps/api/app/profiles/router.py) shift from
"average per submission across all submissions" to "best per mission":

```python
# Pseudocode for _fetch_stats:
# For each mission the user has graded:
#   pick the submission with the highest total_score (ties → most recent)
# Aggregate radar_averages over that subset.
```

Submissions with `score_cap_reason = 'gave_up'` (see P0-4) are **excluded**
from the per-mission best calculation if any non-gave-up attempt exists.
A gave-up attempt is the user's best only when it's the only attempt.

The Skills/mastery view in
[profiles/router.py:get_my_skills](apps/api/app/profiles/router.py) gets
the same dedupe: `sessions_passed` counts distinct missions where the
best non-gave-up attempt cleared threshold, not raw submissions.

### Edge cases

- **User retries a mission they passed.** The new attempt's score does
  not lower their public score even if it's worse (best-per-mission).
  It does change `latest_score` and the delta on the private surface.
- **Rate limit.** Submissions are capped at 3/hour
  ([docs/security.md](docs/security.md)). Retry is bound by the same
  limit. The 429 banner in the UI explains the wait.
- **Replay during active session.** Blocked by the existing
  `active_session_exists` 409 check
  ([sessions/router.py:164](apps/api/app/sessions/router.py#L164)).
  The retry CTA is disabled until the active session is graded or
  abandoned.
- **`previous_session_id` references a session that was hard-deleted**
  (per P0-6). The FK is `ON DELETE SET NULL` so the link gracefully
  breaks; the attempt index still increments correctly because it's
  pre-computed.

### Testing

- Pytest `test_attempt_index_backfill.py` — apply migration on a
  fixture DB with 3 prior sessions; assert correct ordering.
- Pytest `test_profile_dedupes_by_mission.py` — given the same user
  with 3 submissions on mission A (scores 60, 78, 65), profile shows
  total_missions=1, best=78, radar built from the score 78 submission
  only.
- Vitest `mission-detail-your-attempts.test.tsx` — signed-in detail
  page renders the attempts strip; anonymous page does not.
- Playwright `retry-flow.spec.ts` — submit mission → report → "Retry"
  → new session → re-submit → report shows your_attempts.count=2.

### ADR

A new
[`docs/adr/0009-multi-attempt-policy.md`](docs/adr/0009-multi-attempt-policy.md):

> **Decision.** Public aggregates use best-per-mission. Private surface
> shows best + latest + delta. Attempt count is never public. Gave-up
> attempts are excluded from the per-mission best calculation when any
> non-gave-up attempt exists.
>
> **Rationale.** Best encourages study-and-retry, which is the
> pedagogical loop. Latest + delta gives the user a private signal of
> their own trajectory. Hiding the public count prevents grinding
> theatre (submitting empty attempts to inflate "tried 47 missions").

[`docs/open-questions.md`](docs/open-questions.md) — OQ-0004 marked
resolved with a link to this ADR.

### Rollout

One PR for the migration + backend aggregation change + ADR. One
follow-up PR for the FE strip and the retry CTA. The migration is
forward-only safe because the backfill is deterministic. No P0
depends on the FE part of P0-3, so it can ship independently.

---

## P0-4. "Give up" with capped ideal-solution reveal

### Goal

A frustrated user can read the ideal solution mid-session in exchange for
a hard cap on their score. This converts "abandon the session and never
learn" into "submit early with a known cap and read the answer."
[OQ-0002](docs/open-questions.md) is closed by an ADR.

The cap is **50/100**, applied at the report-total level, with a
`score_cap_reason: "gave_up"` field on the submission so the UI can render
the cap honestly.

### Architecture

```
[ apps/api/alembic/versions/0014_give_up.py ]
    + sessions.gave_up_at TIMESTAMPTZ NULL
    + (submissions.score_cap_reason — already added by 0013, see P0-3)

[ apps/api/app/sessions/router.py ]
    + POST /sessions/{id}/give-up   – emits session.gave_up, sets gave_up_at,
                                       triggers an immediate submit.

[ apps/api/app/grading/runner.py ]
    – if session.gave_up_at is not null:
        score_report.total = min(score_report.total, 50)
        score_report.score_cap_reason = "gave_up"
        submission.score_cap_reason = "gave_up"

[ apps/web/components/workspace/WorkspaceTopBar.tsx ]
    + Give-up affordance (gated by 10-min soft block)

[ apps/web/components/workspace/GiveUpDialog.tsx ]
    + Confirm modal that explains the cap
```

### Data model (migration 0014)

```sql
ALTER TABLE sessions
    ADD COLUMN gave_up_at TIMESTAMPTZ NULL;

-- (score_cap_reason on submissions is added by migration 0013 above; if
-- 0014 ships first, swap the order.)
```

### API surface

```
POST /api/v1/sessions/{session_id}/give-up
  body: {}
  preconditions:
    - caller owns the session
    - session.status == 'active'
    - now() - session.started_at >= 10 minutes
  side-effects:
    1. INSERT supervision_event session.gave_up with payload
       {seconds_into_session: int}
    2. UPDATE sessions SET gave_up_at = now()
    3. Internally call the existing submit_session(...) path
       (sessions/submit.py) — runs validators, computes score, persists
       Submission with score_cap_reason='gave_up'.
  response: 200 SubmissionRead (the submit result)
  errors:
    409 if session is not active
    400 with detail "give-up requires at least 10 minutes in session" if
        the 10-min soft block has not elapsed; the body carries
        seconds_remaining for the FE to render a countdown.
```

The 10-minute gate is enforced server-side. The FE also disables the
button until the gate elapses to avoid a roundtrip, but the server is
the source of truth.

### Frontend surface

[`WorkspaceTopBar.tsx`](apps/web/components/workspace/WorkspaceTopBar.tsx)
gains a secondary action button on the right edge:

```
[ Submit ▶ ]   [ Give up & reveal ]
```

Before 10 minutes elapse: the button is rendered but disabled, with a
tooltip "Available in 6m 42s". After 10 minutes: the button is enabled
and opens `GiveUpDialog`.

The dialog reads:

```
Give up and reveal the ideal solution?

This caps your score for this attempt at 50/100. The submission still
shows up on your profile with a "gave up" chip — no hiding. You can
retry the mission later for a clean attempt.
```

Two CTAs: "Stay in the mission" (close) and "Yes, give up". The latter
calls the endpoint, which transitions the session through the existing
`submitting` → `graded` flow (the WorkspaceShell already renders the
`GradingWait` screen for `submitting`).

The report page
([ReportView.tsx](apps/web/components/report/ReportView.tsx)) adds a chip
in the header when `score_report.score_cap_reason === "gave_up"`:

```
70 / 100      Failure mode identified · 3 of 4 hidden tests passing
              ⚑ gave up at 14:32 — score capped at 50/100
```

The chip lives next to (or below) the existing pass/fail indicator.

### Scoring interactions

The runner reads `session.gave_up_at` after computing the dimension
scores. The dimension scores themselves are *not* mutated — they
reflect honest work. The cap is applied at the final `total = sum(...)`
step:

```python
total = sum(d.score for d in dimensions.values() if not d.pending)
if session.gave_up_at is not None:
    if total > 50:
        score_report.score_cap_reason = "gave_up"
    total = min(total, 50)
score_report.total = total
```

`missed_failure_mode` and badges remain computed honestly. A gave-up
attempt can still earn `regression-test-writer` if the user added a
regression test before giving up. The cap is only about the total
shown.

The Skills view (P0-3) and the profile radar exclude gave-up attempts
when computing best-per-mission so a strong real attempt isn't shadowed
by a 50-cap.

### Edge cases

- **User clicks "Give up" with `submit` already in flight.** Server
  guard: session must be in `active` state. Return 409 and tell the FE.
- **User clicks "Give up" then the sandbox reaper kills the container
  mid-grade.** The give-up event is already emitted; the session has
  `gave_up_at` set. The runner's timeout handling (see
  [grading/runner.py:DEFAULT_BUDGET_SECONDS](apps/api/app/grading/runner.py))
  produces the same error report path as any other grade failure —
  but with `score_cap_reason="gave_up"` still applied.
- **User retries after giving up.** A new session starts at
  `attempt_index += 1` (per P0-3). The new attempt is uncapped. Their
  best-per-mission profile score reflects the better of the two.
- **Multiple give-ups across attempts.** Public profile shows the best
  *non-gave-up* score if any; otherwise the best gave-up score.
- **User opens the workspace, sits idle for 11 minutes, then gives up.**
  The 10-min gate elapsed; the give-up succeeds; the report is honest
  about how little they tried.

### Testing

- Pytest `test_give_up_blocked_before_window.py` — POSTing before 10
  min returns 400 with `seconds_remaining` in the body.
- Pytest `test_give_up_caps_total.py` — a fixture submission that would
  otherwise score 82 is capped to 50 when `gave_up_at` is set.
- Pytest `test_give_up_does_not_mutate_dimensions.py` — the dimension
  scores remain 82's underlying values; only `total` and
  `score_cap_reason` differ.
- Vitest `give-up-dialog.test.tsx` — modal copy, disabled state during
  the 10-min window with a live-updating countdown.
- Playwright `give-up.spec.ts` — start mission, advance system clock
  in test mode, give up, land on report with chip.

### ADR

[`docs/adr/0010-give-up-policy.md`](docs/adr/0010-give-up-policy.md):

> **Decision.** Score cap 50, 10-minute soft block, no hiding from
> profile. Dimension scores remain honest; cap is applied only at the
> total. Skills/radar aggregation excludes gave-up attempts when a
> non-gave-up attempt exists.

[`docs/open-questions.md`](docs/open-questions.md) — OQ-0002 marked
resolved.

### Rollout

Single PR. Depends on migration 0013 only because both add the
`score_cap_reason` column; whichever migration ships first owns the
column.

### Open decisions

- **Should the 10-minute gate be configurable per-mission?** Advanced
  missions might warrant 20 minutes. Recommendation: per-mission
  override in `mission.yaml` (`give_up_after_seconds: 600`), with a
  global default. Defer to first piece of user feedback that this is
  too short or too long for some scenarios.

---

## P0-5. Legal pages + cookie consent

### Goal

The product is legally operable in the EU (GDPR), UK (UK-DPA),
California (CCPA/CPRA), Canada (PIPEDA), and Brazil (LGPD). Every PII or
behavioural-data flow has a documented basis. Non-essential telemetry
emits only with an explicit user opt-in that survives both local and
server-side records.

### Architecture

```
[ apps/web/app/(marketing)/legal/ ]
    terms/page.tsx        – ToS (MDX or markdown loaded at build)
    privacy/page.tsx      – Privacy policy
    cookies/page.tsx      – Cookie list + opt-in / opt-out

[ apps/web/components/legal/ ]
    CookieConsentBanner.tsx       – first-visit banner (client-only)
    ConsentRecorder.tsx           – server-sync wrapper (on consent change)

[ apps/web/lib/ ]
    consent.ts                    – getConsent() / setConsent() /
                                    useConsent() hook reading localStorage
                                    + a server-side record
    telemetry.ts (existing)       – gated on consent.analytics === true

[ apps/api/alembic/versions/0015_user_consents.py ]
    + user_consents table

[ apps/api/app/auth/routes.py ]
    + POST /me/consent           – record consent server-side
    + GET  /me/consent           – fetch current state
```

### Data model (migration 0015)

```sql
CREATE TABLE user_consents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL
        CHECK (kind IN ('analytics', 'functional', 'marketing')),
    granted         BOOLEAN NOT NULL,
    version         INTEGER NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address_hash TEXT NULL,            -- SHA-256 of remote_addr
    user_agent      TEXT NULL,
    UNIQUE (user_id, kind, version)
);

CREATE INDEX idx_user_consents_user_kind
    ON user_consents (user_id, kind, granted_at DESC);
```

`version` increments any time the underlying policy text changes. When
the user's stored version is below the current `LATEST_CONSENT_VERSION`,
the cookie banner re-shows with "Our cookie policy changed — please
re-confirm."

### API surface

```
GET  /api/v1/me/consent
  response:
    {
      "analytics": { "granted": bool, "version": int, "at": iso8601 } | null,
      "functional": { ... },
      "marketing": { ... }
    }

POST /api/v1/me/consent
  body:
    { "kind": "analytics" | "functional" | "marketing", "granted": bool }
  side-effects:
    INSERT user_consents row (with the current LATEST_CONSENT_VERSION)
  response: 204
```

`functional` is essential cookies (session cookie, CSRF) — the banner
explains they cannot be opted out of. `marketing` exists for future
expansion; ships unused.

### Frontend surface

**Cookie banner** — appears on first visit (no `consent_v` in
localStorage) and any time `consent.version < LATEST_CONSENT_VERSION`:

```
┌────────────────────────────────────────────────────────────────┐
│ // cookies                                                     │
│ We use essential cookies to keep you signed in and to protect  │
│ you against CSRF. Optional analytics cookies help us improve   │
│ the product. You can change this in /account/privacy at any    │
│ time.                                                          │
│                                                                │
│ [ Accept all ]  [ Essential only ]  [ Customize ]              │
└────────────────────────────────────────────────────────────────┘
```

"Customize" opens a small dialog with one toggle per cookie kind plus a
link to `/legal/cookies`. Choices are written to localStorage *and* (if
signed in) `POST`ed to `/me/consent` so server-side consent records
match.

**Footer link block.** [Footer.tsx](apps/web/components/marketing/Footer.tsx)
adds a fourth column:

```
Legal
- Terms of service
- Privacy policy
- Cookies
- Data subject request
```

"Data subject request" deep-links to `/account` (P0-6) for signed-in
users; for anonymous it links to a contact form / email.

**Header dropdown.** The signed-in dropdown (P0-1 already adds entries
here) gains a "Privacy" item that routes to `/account/privacy`. This is
the in-app surface to toggle the consent flags without leaving the
product.

### Telemetry gating

[`apps/web/lib/telemetry.ts`](apps/web/lib/telemetry.ts) is wrapped:

```typescript
import { getConsent } from "./consent";

export function track(event: string, props?: Record<string, unknown>) {
  if (!getConsent().analytics) return; // essential-only by default
  // existing emit logic
}
```

The PostHog/OTEL client is initialized only after consent is granted.
Until then, calls are no-ops. This is the "essential-only by default"
posture.

### Content checklist (what each page must say)

The legal copy itself is a lawyer's job. The structure must include:

**Privacy policy (`/legal/privacy`):**

1. **What we collect.** Email, optional display name, optional GitHub
   login (via OAuth — P0-7, out of scope here). Behavioural telemetry
   (events, prompts, command output) tied to your session. IP address
   at sign-in (for rate-limit and abuse prevention).
2. **Why.** Magic-link delivery (email), profile rendering (handle),
   scoring (events/prompts), abuse prevention (IP).
3. **How long.** Submission and session data retained indefinitely or
   until the user invokes deletion. Logs purged at 90 days. IP hashes
   purged at 7 days.
4. **Processors.** Resend (email delivery), AWS Bedrock (LLM narration
   — only if `features.llm_narration_enabled` is on), Fly.io (hosting),
   Cloudflare R2 (artifact storage), Upstash Redis (queue), Neon /
   Fly Postgres (database).
5. **Your rights.** Article 15 access, Article 17 erasure, Article 20
   portability — all implementable via `/account` (P0-6).
6. **Contact.** A real DPO email address. Not optional.

**Terms of service (`/legal/terms`):** standard sandbox-usage
provisions, prohibition on running malicious workloads in the sandbox,
fair use rate limits, IP retention, and a no-warranty clause.

**Cookies page (`/legal/cookies`):** itemized list:

| Cookie | Kind | Purpose | Lifetime |
|---|---|---|---|
| `arena_session` | functional | Auth + CSRF | 30 days |
| `csrftoken` | functional | CSRF double-submit | session |
| `consent_v` | functional | Records consent choice | 365 days |
| `_posthog` (or equivalent) | analytics | Telemetry | per provider |

### Edge cases

- **Anonymous user accepts cookies; later signs up.** The localStorage
  consent record is copied to a `user_consents` row on first
  authenticated request. The two states are reconciled.
- **User opts out of analytics mid-session.** The next telemetry call
  is a no-op. Existing in-flight events are still sent — no rollback
  is offered or promised.
- **Policy version bumps.** Banner re-appears; until the user
  re-confirms, optional analytics events are suppressed (treated as
  revoked).
- **Compromised account.** Account self-service (P0-6) allows password-
  free email change; consent record is preserved because it's keyed
  by `user_id`, not by email.
- **Deletion request (P0-6).** All `user_consents` rows cascade-delete;
  the user's consent history is destroyed along with their account.

### Testing

- Pytest `test_consent_endpoints.py` — GET reflects POST; replay POSTs
  insert new rows with monotonic `version`; CASCADE on user delete.
- Vitest `cookie-banner.test.tsx` — banner renders on first visit,
  hides after Accept/Essential, re-renders when version bumps.
- Playwright `consent-flow.spec.ts` — accept cookies → analytics
  events fire; opt out → events stop.

### Rollout

Sequence:

1. PR1 — backend migration + `/me/consent` endpoints + cookie banner +
   consent gating in `telemetry.ts`. The legal pages return
   placeholder copy ("Draft — see /docs/legal-draft.md").
2. Lawyer review.
3. PR2 — final legal copy lands in `apps/web/app/(marketing)/legal/`.

Once PR1 ships, the product is technically compliant (no analytics
without consent); PR2 makes it legally honest.

---

## P0-6. Account self-service

### Goal

Every signed-in user can manage their account end-to-end without contacting
support: change email, sign out everywhere, export their data, and delete
their account on a 7-day grace timer. The "right to be forgotten" is a
button, not an email thread.

### Architecture

```
[ apps/web/app/(app)/account/ ]
    page.tsx                         – server component, wraps AccountView
    privacy/page.tsx                 – consent toggles (cross-links P0-5)

[ apps/web/components/account/ ]
    AccountView.tsx                  – tabbed shell (profile / privacy /
                                       data / danger)
    ProfileForm.tsx                  – display_name change
    EmailChangeForm.tsx              – two-step magic-link flow
    SignOutAllButton.tsx             – invalidates all sessions
    DataExportPanel.tsx              – kicks async job, polls status
    DeleteAccountDialog.tsx          – 7-day grace, confirmation email

[ apps/api/alembic/versions/0016_account_self_service.py ]
    + users.pending_email TEXT NULL
    + users.deletion_scheduled_at TIMESTAMPTZ NULL
    + data_exports table

[ apps/api/app/auth/routes.py ]
    + PATCH  /me                     – partial: display_name
    + POST   /me/email/change        – step 1: queue magic-link to new email
    + POST   /me/email/confirm       – step 2: magic-link callback
    + POST   /me/sessions/sign-out-all
    + POST   /me/data-export         – kick async job
    + GET    /me/data-export/{id}    – poll status / get signed URL
    + POST   /me/delete              – schedule deletion (7-day grace)
    + POST   /me/delete/cancel       – cancel deletion during grace

[ apps/api/app/workers/ ]
    + account_export.py              – RQ job: zip user data, upload to R2,
                                       return signed URL valid 7 days
    + account_deletion.py            – RQ scheduled job (daily): hard-delete
                                       accounts whose grace expired
```

### Data model (migration 0016)

```sql
ALTER TABLE users
    ADD COLUMN pending_email CITEXT NULL,
    ADD COLUMN deletion_scheduled_at TIMESTAMPTZ NULL;

CREATE TABLE data_exports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status        TEXT NOT NULL
                  CHECK (status IN ('queued','running','ready','failed','expired')),
    s3_key        TEXT NULL,                  -- set when status='ready'
    bytes_total   BIGINT NULL,
    error         TEXT NULL,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at      TIMESTAMPTZ NULL,
    expires_at    TIMESTAMPTZ NULL,           -- 7 days after ready_at
    UNIQUE (user_id, status) WHERE status IN ('queued','running')
);

CREATE INDEX idx_data_exports_user ON data_exports (user_id, requested_at DESC);
```

The unique partial index enforces "one in-flight export per user at a
time." Completed exports stack; the panel shows the latest.

### API surface

```
GET /api/v1/me
  → existing User shape PLUS:
    pending_email: string | null
    deletion_scheduled_at: iso8601 | null

PATCH /api/v1/me
  body: { display_name?: string }
  rules:
    handle changes are NOT supported (handle is taken at signup; collision-
    avoidance + profile-URL stability outweigh self-service flexibility for
    MVP)
  response: 200 User

POST /api/v1/me/email/change
  body: { new_email: string }
  side-effects:
    1. validate new_email shape; reject if already in users.email or
       users.pending_email of any account
    2. set users.pending_email = new_email
    3. send a magic link to new_email signed for the existing user_id
       with kind='email-change'
    4. emit account.email_change_requested event (payload hashes the
       email to avoid logging it in the event payload directly)
  response: 204

POST /api/v1/me/email/confirm
  body: { token: string }
  side-effects:
    1. decode token, assert kind='email-change' and sub==current user
    2. UPDATE users SET email=pending_email, pending_email=NULL
    3. invalidate all existing session cookies (sign out other devices)
    4. log the caller back in with a fresh cookie
  response: 200 User

POST /api/v1/me/sessions/sign-out-all
  side-effects:
    1. rotate users.cookie_secret_salt (or equivalent) so every existing
       signed cookie except the current one (re-signed with new salt
       immediately) fails verification on the next request
  response: 204

POST /api/v1/me/data-export
  side-effects:
    enqueue an account_export RQ job
  response: 202 DataExportRead { id, status: 'queued', requested_at }

GET /api/v1/me/data-export/{id}
  response: DataExportRead
    { id, status, requested_at, ready_at?, expires_at?, download_url? }
  download_url is a signed R2 URL valid for the remainder of expires_at

POST /api/v1/me/delete
  body: { confirm_email: string }       — must match users.email
  side-effects:
    1. set users.deletion_scheduled_at = now() + 7 days
    2. send a confirmation email with a "cancel deletion" link
    3. invalidate all existing sessions
    4. emit account.deletion_scheduled
  response: 200 { scheduled_for: iso8601 }

POST /api/v1/me/delete/cancel
  side-effects:
    if users.deletion_scheduled_at > now():
        set users.deletion_scheduled_at = NULL
        emit account.deletion_cancelled
    else:
        return 410 "deletion already processed"
  response: 204
```

### Export bundle contents

The export job (`apps/api/app/workers/account_export.py`) produces a
zip file `arena-export-{user_id_short}-{iso_date}.zip` containing:

```
README.md                  – plain-English explanation of every file
user.json                  – the User row, scrubbed of internal fields
                              (cookie salts, etc.)
sessions.jsonl             – one line per Session
agent_turns.jsonl          – every prompt/response the user sent/received
file_changes.jsonl
command_runs.jsonl         – with stdout/stderr keys, not the raw text
                              (those live in S3; the export includes signed
                              URLs valid for the export's lifetime)
submissions.jsonl
supervision_events.jsonl
badges.jsonl
consents.jsonl             – the user_consents history
```

Format choice: JSONL because it's what the supervision-event log is
already designed for, and it's the format the (future) replay tool will
consume. PostHog/analytics events are *not* in the export — the user
exports the data the product persists, not third-party aggregations.

### Frontend surface

`/account` is a tabbed layout:

```
[ Profile ] [ Privacy ] [ Data ] [ Danger ]

# Profile
- Display name: Jane Doe                                      [ Save ]
- Handle: @jane (read-only, set at signup)
- Email: jane@example.com                          [ Change email ]
  If pending: "Pending: jane.doe@gmail.com (confirm via the link we sent)"
- Joined: 2026-04-09
- Sign out everywhere                                 [ Sign out all ]

# Privacy
- Analytics cookies         [ on/off ]
- Marketing emails          [ on/off ] (placeholder for future)
- See current policy →

# Data
- Download my data
  - Latest export: ready 2 hours ago · expires in 6d 22h  [ Download ]
  - Or: [ Request new export ]
- Replay artifacts
  - Per-submission replay JSON download (one-click per row)

# Danger
- Delete my account
  [ Start 7-day deletion process ]
  If scheduled: "Deletion scheduled for 2026-06-04. [ Cancel ]"
```

The Danger tab is visually red-walled to slow down accidental clicks. The
deletion confirmation requires re-typing the user's email — same friction
GitHub uses.

### Edge cases

- **Email-change conflict.** Two users race to claim
  `new@example.com` as their pending_email. Server enforces uniqueness
  on `email` and `pending_email` — second user gets a 409 "in use."
- **Email-change link expires** (30 min, same as sign-in). The user can
  re-request from the form; `pending_email` is overwritten.
- **Pending email-change while deletion is scheduled.** Forbidden: the
  email-change endpoint rejects if `deletion_scheduled_at is not null`.
  Cancel the deletion first.
- **Deletion grace expires while user has an active session.** The
  scheduled job tears down the sandbox, marks the session abandoned,
  cascade-deletes everything, then tombstones the email
  (`users.email = 'deleted-{user_id_short}@deleted.openagentdojo.app'`).
- **Export job fails** (zip too large, R2 outage). Status goes to
  `failed` with an `error` message. The user can request another export
  immediately. Failed exports never block new requests.
- **Concurrent export requests.** Unique partial index rejects a second
  queued/running export — UI says "an export is in flight."
- **R2 signed URL expires before download.** Same export remains in
  `ready` state until `expires_at`. After that, status flips to
  `expired` via a daily job; user can request another.
- **`previous_session_id` (P0-3) dangling FK after delete.** Already
  handled by `ON DELETE SET NULL` on the FK.
- **Profile becomes 404 after delete.** The handle is also tombstoned
  (`users.handle = 'deleted-{user_id_short}'`), so anyone visiting
  `/profile/{old_handle}` gets a clean 404 with no leakage.

### Security

- **The deletion path is `POST`-only and CSRF-protected** (already the
  rule for every mutating endpoint per
  [docs/security.md](docs/security.md)).
- **The signed R2 URL is short-lived** (7 days, matching the export's
  expires_at). Never logged.
- **The data-export zip never contains** Bedrock bearer tokens, session
  cookie secrets, or other user's data. The `account_export.py` job
  reads only rows where `user_id = export.user_id`.
- **The deletion grace cannot be circumvented** by re-signing in; the
  user can sign in during the grace (so they can cancel), but every
  POST except `/me/delete/cancel` returns 403 with a "deletion
  scheduled" message.

### Testing

- Pytest `test_account_email_change_two_step.py` — happy path and
  conflict path.
- Pytest `test_account_deletion_grace.py` — schedule deletion, sign in
  during grace, cancel, sign in normally; or schedule, fast-forward
  clock, assert hard-delete and 404 on profile + `previous_session_id`
  set to null on prior attempts referenced from another user's history
  (defensive).
- Pytest `test_data_export_bundle.py` — runs the job, asserts every
  JSONL file is non-empty for a fixture user, asserts no PII leakage
  for unrelated users.
- Vitest `account-view.test.tsx` — tab navigation, pending-email
  rendering, scheduled-deletion countdown.
- Playwright `account-flow.spec.ts` — sign in → /account → request
  export → poll until ready → download.
- Playwright `account-delete.spec.ts` — schedule delete → cancel →
  schedule again → wait for processing (in test-mode time).

### Rollout

Three PRs in series:

1. PR1 — migrations + endpoints + the `/account` shell (no Data tab,
   no Danger tab). Profile + Privacy work.
2. PR2 — Data tab (export job, worker, panel).
3. PR3 — Danger tab (deletion grace + scheduled worker job).

Each PR ships behind no feature flag — the account page is *useful* at
PR1, and the bigger pieces add to it.

### Open decisions

- **Tombstoning vs full delete of the email column.** Tombstoning lets
  us prove "this account existed and was deleted on date X" without
  resurrecting it. Full delete is cleaner but loses the audit trail.
  Recommendation: tombstone, because the lawyer will want the audit
  trail. Document in the Privacy Policy.
- **Should the export include `prompts` text in cleartext?** The
  prompts are the user's words and they're entitled to them. But the
  Privacy Policy must explicitly call this out so the user can
  exercise the right to scrub them before, e.g., uploading the zip to
  a third party.
- **Handle changes.** Not supported in MVP per the design above. A
  handle is a public credential URL; changing it would break inbound
  links from résumés/LinkedIn. Re-evaluate after first user feedback.

---

## A. Dependency graph (which P0 depends on which)

```
P0-1 (tutorial)         — independent
P0-2 (post-mortem)      — independent, but assumes mission backfill of
                          ideal_solution.diff (one PR)
P0-3 (multi-attempt)    — adds submissions.score_cap_reason (shared with P0-4)
P0-4 (give up)          — depends on P0-3's column if 0013 ships first;
                          otherwise owns 0014 with the column
P0-5 (legal + consent)  — independent
P0-6 (account)          — independent of all others

Recommended ship order (minimizes user-visible regressions):
  P0-5 (legal compliance unlock)
→ P0-6 (account self-service)
→ P0-1 (onboarding) and P0-3 (multi-attempt) in parallel
→ P0-2 (post-mortem) once the mission backfill PR lands
→ P0-4 (give up) last — it's the smallest behavioral addition once the
   column from P0-3 is in.
```

---

## B. What stays the same

All of the load-bearing architectural invariants are preserved:

- **Determinism on the grading path.** No P0 introduces an LLM call to
  the grader. The post-mortem (P0-2) reuses the same event log the
  grader already consumes. The give-up cap (P0-4) is a pure
  post-processing step.
- **Event-sourcing.** Every new user action emits a typed supervision
  event. The replay artifact (FEATURE_GAPS P1-6) will see all of them.
- **Mission manifest as content contract.** P0-1 (`kind`), P0-2
  (`ideal_solution.diff`), and the future P0-4 per-mission `give_up_after_seconds`
  override are all schema-additive. The validator (`pnpm
  validate:missions`) catches drift in CI.
- **Process-only score preview.** P0-2 only changes the *post-submit*
  report. ScorePreview during the session is untouched. The "hidden
  things stay hidden until submit" promise holds.
- **Sandbox isolation.** No change.

— design authored against branch `codex/goal`, 2026-05-23.
