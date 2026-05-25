# OpenAgentDojo — P0 Implementation Design (Part II: items 11–13)

Continues [P0_DESIGN.md](P0_DESIGN.md) (which covered P0-1 through P0-6).
Same template per item: goal, architecture, data model, API contract,
frontend surface, scoring/telemetry interactions, edge cases, testing,
rollout, open decisions. The shared sections of P0_DESIGN.md (§0
cross-cutting decisions, §A dependency graph, §B what stays the same) apply
here too — this document only adds new constraints.

---

## 0. Where this batch slots into the migration timeline

P0_DESIGN.md reserved migrations 0011 through 0016. This batch claims:

| Migration | File | P0 item | Adds |
|---|---|---|---|
| 0017 | `0017_report_verification.py` | P0-11 | `submissions.verification_hash`, `submissions.verification_signature`, `report_renders` table |
| 0018 | `0018_session_reset_event.py` | P0-12 | (no new columns — documents the `session.reset` event type and a covering index) |

P0-13 is documentation-only — no schema changes.

The new `session.reset` event type joins the existing supervision event
catalogue (P0_DESIGN.md §0.3). The new `report.rendered` /
`report.verified` events are *system* events (no `session_id`); they live
in the existing telemetry pipeline rather than `supervision_events`.

---

## P0-11. Exportable / verifiable report artifact

### Goal

The user — or anyone they hand a URL to — can prove a graded submission
exists, who it belongs to, what it scored, and what failure mode it caught,
without a sign-in and without the URL ever expiring. The user can also
download a print-fidelity PDF (résumé bullet attachment) and a
LinkedIn-sized PNG (social share). The verification primitive is the
credentialing artifact P0_DESIGN P0-7 (identity verification) and P0-8
(anti-cheating) plug into; this item ships the artifact itself.

Three concrete outputs:

1. **`/verify/{submission_id}`** — a permanent, anonymous, minimal-fields
   page. Cacheable indefinitely; hashed and signed so a recruiter can
   confirm "this report was issued by the platform, not fabricated."
2. **PDF download** — pixel-faithful to the live report, embeds the
   verification hash + a QR code linking back to the verify page.
3. **PNG download** — the existing
   [`opengraph-image.tsx`](apps/web/app/(app)/report/[submissionId]/opengraph-image.tsx)
   surface, exposed as an explicit download.

### Architecture

```
[ apps/api/alembic/versions/0017_report_verification.py ]
    + submissions.verification_hash       TEXT NOT NULL
    + submissions.verification_signature  TEXT NOT NULL
    + report_renders                       table (cached PDF/PNG metadata)

[ apps/api/app/grading/runner.py ]
    on grading completion, compute verification_hash + signature from a
    canonical, sorted, minimal envelope (see §11.2 envelope shape) and
    persist them on the Submission row. Deterministic — same envelope,
    same submission, same hash on every replay.

[ apps/api/app/reports/router.py ]
    + GET  /verify/{submission_id}                – public; envelope only
    + GET  /reports/{submission_id}/render        – auth same as report
                                                     get; redirects to
                                                     signed R2 URL
    + POST /reports/{submission_id}/render        – owner only; force
                                                     re-render

[ apps/api/app/workers/report_render.py ]
    RQ worker. Singleton Chromium per worker process. Visits the internal
    print-mode page; emits PDF or PNG; uploads to R2; records in
    report_renders.

[ apps/web/app/verify/[submissionId]/page.tsx ]
    Public verify page. No (app) layout. ISR with revalidate: false.

[ apps/web/app/(internal)/report-print/[submissionId]/page.tsx ]
    Print-mode HTML rendered by the worker only. Gated by X-Render-Token
    header so it is not publicly reachable.

[ apps/web/components/report/ReportHeader.tsx ]
    Existing "[ Share report ]" button becomes a dropdown:
      Copy share link · Download PDF · Download PNG · Open verify page
```

### Choice: server-side Chromium vs alternatives

Considered:
- **WeasyPrint / Prince** — HTML→PDF without a browser. Fast (no chromium),
  but the report uses CSS features (`oklch()`, `backdrop-filter`,
  recharts SVG) that Prince/Weasy don't fully support. Result would
  drift from the live report — exactly what we are trying not to
  promise.
- **Client-side `window.print()`** — user keeps the file. Free for us.
  But: the PDF then doesn't carry a server-signed footer (the user could
  edit it before sharing), so it cannot function as a credential.
- **Server-side Chromium via Playwright** — pixel-perfect parity with the
  live page; one chromium per worker process (~250 MB RSS, acceptable);
  same dependency the e2e suite already pulls. This is the choice.

The PDF is rendered by visiting an internal route
`/report-print/{submission_id}?token=...` that re-renders the existing
[`ReportView`](apps/web/components/report/ReportView.tsx) in a
print-friendly layout (no chrome, white background, embedded fonts, page
breaks at section boundaries). The worker then calls
`page.pdf({format: 'Letter', printBackground: true})`.

### Data model (migration 0017)

```sql
ALTER TABLE submissions
    ADD COLUMN verification_hash TEXT NOT NULL DEFAULT '',
    ADD COLUMN verification_signature TEXT NOT NULL DEFAULT '';

-- Backfill: compute deterministically for every graded submission.
UPDATE submissions s
SET (verification_hash, verification_signature) = (
    SELECT
        sha256_hex,
        hmac_sha256_hex(sha256_hex, current_setting('app.verify_secret'))
    FROM compute_envelope(s.id)
)
WHERE s.total_score IS NOT NULL;

-- Lift the defaults so new rows are forced through the runner.
ALTER TABLE submissions
    ALTER COLUMN verification_hash DROP DEFAULT,
    ALTER COLUMN verification_signature DROP DEFAULT;

CREATE TABLE report_renders (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('pdf', 'png')),
    status        TEXT NOT NULL CHECK (status IN ('queued','running','ready','failed')),
    s3_key        TEXT NULL,
    bytes         INTEGER NULL,
    error         TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at      TIMESTAMPTZ NULL,
    UNIQUE (submission_id, kind)
);

CREATE INDEX idx_report_renders_status ON report_renders (status, created_at);
```

The `UNIQUE (submission_id, kind)` constraint plus `INSERT … ON CONFLICT DO
UPDATE` enforces "one PDF and one PNG per submission, latest wins." A
force re-render (`POST /render`) overwrites both rows.

Backfill SQL uses a hypothetical `compute_envelope()` helper — in
practice the backfill ships as a one-shot Python script (`scripts/backfill_verification.py`)
that reads each submission, builds the envelope in Python, and updates
the row. Pure-SQL backfill is brittle for nested JSONB.

### Verification envelope shape

A canonical JSON object, sorted by key, with these and only these fields:

```json
{
  "schema_version": 1,
  "submission_id": "7c4123ab-…",
  "handle": "jane",
  "display_name": "Jane Doe",
  "mission_id": "auth-cookie-expiration",
  "mission_title": "Expired Session Cookie Still Grants Access",
  "mission_version": 1,
  "rubric_version": "v1",
  "total_score": 78,
  "effective_max": 100,
  "missed_failure_mode": false,
  "score_cap_reason": null,
  "proctored": false,
  "attempt_index": 2,
  "graded_at": "2026-05-23T18:42:11Z"
}
```

- **`rubric_version`** is critical. It pins the score to the rubric that
  produced it. If the rubric is re-balanced later, old verification pages
  render with a small note: "scored under rubric v1 (current: v2)."
- **`mission_version`** detects "the mission content changed under your
  feet." A re-issued envelope after a mission edit has a different
  hash; old PDFs become stale (see Open decisions).
- **`schema_version`** future-proofs the envelope itself.

The `verification_hash` is `sha256(JSON.canonical(envelope))`. The
`verification_signature` is `hmac_sha256(verification_hash, VERIFY_SECRET)`.
`VERIFY_SECRET` is a new secret (rotated independently from
`SESSION_SECRET` so report verification survives a session-secret
rotation), managed in Fly secrets the same way as the others.

### API surface

```
GET /api/v1/verify/{submission_id}
  No auth required. Public. Cacheable for 1 year.
  Errors:
    404 if submission is not graded, is tutorial-kind, or the user has
        been deleted (deletion tombstones the handle so the verify page
        can still render with handle="deleted-{short}", display_name=null
        — see "Edge cases" below for the exact behaviour)
  Response: the envelope above + canonical_url + verification_hash + signature

GET /api/v1/reports/{submission_id}/render?kind=pdf
  Auth: same as GET /reports/{submission_id} (owner OR ?share=).
  Behaviour:
    Look up report_renders for (submission_id, kind).
    - status=ready    → 302 redirect to a 5-minute signed R2 URL
    - status=queued/running → 202 { status, poll_after_seconds: 5 }
    - status=failed   → 503 { error } (FE shows retry banner)
    - row missing     → enqueue a render job, return 202

POST /api/v1/reports/{submission_id}/render
  Auth: owner only.
  Body: { kind: 'pdf' | 'png' }
  Force a re-render (deletes the existing report_renders row, enqueues a
  fresh job). Idempotent — repeat calls are no-ops while status is queued
  or running.
  Response: 202 { render_id, status: 'queued' }
```

The render worker is a new RQ task in
`apps/api/app/workers/report_render.py`, alongside the existing
`provision.py` worker.

### Frontend surface

#### `/verify/{submission_id}` page

A standalone Next.js route in `apps/web/app/verify/[submissionId]/page.tsx`
(no `(app)` or `(marketing)` layout — a third, minimal layout). The
page is server-rendered with `revalidate: false` (graded submissions are
immutable). Layout:

```
┌──────────────────────────────────────────────────────────────┐
│ // verified report · openagentdojo.app                       │
│                                                              │
│              78 / 100                                        │
│              Mission · auth-cookie-expiration                │
│              @jane (Jane Doe) · attempt 2                    │
│                                                              │
│  ✓ Failure mode identified · 3 of 4 hidden tests passing     │
│  Graded · 2026-05-23T18:42:11Z · rubric v1                   │
│                                                              │
│  ────────────────────────────────────────────────────────    │
│                                                              │
│  ✓ Issued by OpenAgentDojo. This page was rendered from a    │
│    server-signed envelope; it cannot be fabricated client-   │
│    side.                                                     │
│                                                              │
│  verification_hash: 3a4b…f12c                                │
│  signature:         0xde…91                                  │
│                                                              │
│  ────────────────────────────────────────────────────────    │
│                                                              │
│  This page intentionally does not show prompts, supervision  │
│  events, or code edits. The full report is available to the  │
│  submission owner.                                           │
│                                                              │
│  [ Open full report (auth required) → ]                      │
└──────────────────────────────────────────────────────────────┘
```

No images. No external requests. No JS-driven hydration except a
"copy hash to clipboard" affordance. The page is intentionally boring —
it is a credential, not a marketing surface.

The page sets `<meta name="robots" content="index, follow">` so it can
appear in Google results when a recruiter searches for the URL — that's
the verification path.

#### Print-mode route

`apps/web/app/(internal)/report-print/[submissionId]/page.tsx` renders
the full `ReportView` with print-mode tweaks:

- No `Header`/`Footer`.
- White background, full-bleed margins from `@page { margin: 16mm; }`.
- Page-break-before on each `<Section>` so the radar, breakdown,
  strengths, and timeline land on their own pages.
- Footer (printed by the worker via `displayHeaderFooter: true` in
  `page.pdf()`) embeds the verification hash + a 1-inch QR code linking
  to the verify URL.
- Embedded JetBrains Mono + Inter (the print PDF must not depend on
  font CDN availability at render time).

The route checks for the `X-Render-Token` header (an HMAC of
`submission_id + render_id` signed with `RENDER_SECRET`); without it,
returns 404. Public visits cannot stumble onto it.

#### Report header dropdown

The existing `[ Share report ]` button becomes a `[ ▼ Share ]` dropdown:

```
┌──────────────────────────────┐
│ Copy share link  (30d expiry)│
│ Download PDF                 │
│ Download PNG  (LinkedIn)     │
│ ────────────────────────     │
│ Open verification page →     │
└──────────────────────────────┘
```

The PDF and PNG entries trigger the render endpoint. If status is
`queued|running`, the menu item shows a spinner and re-polls every 5s.

### Scoring / telemetry interactions

Grading produces the verification hash + signature deterministically; the
rubric/score logic itself is unchanged. New telemetry events:

| Event | When | Payload |
|---|---|---|
| `report_render_requested` | dropdown click | `{kind, cache_hit}` |
| `report_render_succeeded` | worker completion | `{kind, ms, bytes}` |
| `report_render_failed` | worker error | `{kind, error_class}` |
| `report_verified` | `/verify/...` page view | `{submission_id, referer_host}` |

`report_verified` is the key acquisition signal — it tells us when a
verify URL is actually consumed by an external party. Referer host
(when present) tells us whether it came from LinkedIn, a résumé site,
etc.

### Edge cases

- **Submission for a tutorial mission (kind=tutorial).** Verify and
  render endpoints return 404 — tutorials are not credentials.
- **Submission with `score_cap_reason="gave_up"`.** Renders normally;
  the envelope's `score_cap_reason` field is non-null so the verify
  page and PDF both show "Score capped at 50/100 (gave up)." Honesty
  by construction.
- **User deletes account (P0_DESIGN P0-6) while a verify URL is in the
  wild.** Submission row stays (delete tombstones the user but does not
  cascade to submissions); handle and display_name are tombstoned. The
  verify page renders `handle: "deleted-7c41"`, `display_name: null`.
  Hash and signature still verify against the *original* envelope
  (recorded at grading time, never recomputed). The PDF embedded in the
  user's résumé continues to verify — which is the right answer for a
  credential.
- **Rubric re-balance.** The grading runner stamps `rubric_version` into
  the envelope at grade time. A re-balance bumps the version. Old
  verify pages render with a one-line note: "Scored under rubric v1
  (current version: v2)." Hash and signature still verify against v1.
- **Mission content edited after grading.** The envelope includes
  `mission_version`. An author bumping a mission version invalidates
  the *interpretation* of old scores but not the *fact* of them. Verify
  page renders, with a small "this mission has been updated since this
  attempt" note.
- **R2 outage.** Verify page works (no R2 dependency). PDF download
  returns 503 with a retry message.
- **Concurrent render requests.** `UNIQUE (submission_id, kind)` on
  `report_renders` plus an idempotent `ON CONFLICT DO NOTHING` insert
  pattern. Second request finds the existing row and waits for it.
- **Chromium OOM in worker.** Worker has hard memory bounds; on
  exhaustion the job marks `status=failed, error="render OOM"` and the
  FE surfaces "PDF generation failed; please retry."
- **VERIFY_SECRET rotation.** Old hashes still verify because the
  signature was computed against the secret-at-grading-time. To
  re-verify legacy reports under a new secret, run a one-shot rotation
  script that re-signs (not re-hashes) every envelope. The hash itself
  is stable across secret rotation.

### Testing

- Pytest `test_verification_envelope_canonical.py` — same submission
  produces the same hash on every call; key ordering does not affect
  the hash; nested JSON is canonicalised.
- Pytest `test_verify_endpoint_public.py` — endpoint requires no auth;
  404 on tutorial / non-graded.
- Pytest `test_render_endpoint_caches.py` — second request hits cache;
  force-render overwrites.
- Pytest `test_render_worker_pdf.py` — integration test that spins a
  real chromium against a fixture report, asserts PDF byte length > 50
  KB and contains the verification_hash as searchable text (using
  pypdf).
- Vitest `verify-page.test.tsx` — minimal envelope renders; missing
  optional fields handled.
- Playwright `pdf-download.spec.ts` — full flow: report page → Share →
  Download PDF → file lands → opens in headless reader.
- Manual: visual diff of PDF vs. live report on Chrome/Firefox/Safari.

### Rollout

Two-step:

1. **PR1 (verification primitive)** — migration 0017, runner
   integration, backfill script, `/verify/{submission_id}` endpoint
   and page. Ships the credential primitive. The verify page works the
   moment this lands.
2. **PR2 (render pipeline)** — `report_render.py` worker, render
   endpoints, print-mode page, the dropdown menu. Adds the
   downloadable artifacts.

PR1 unblocks the credentialing claim alone. PR2 is the polish but
also the heavier infrastructure (Chromium in the worker pool).

### Open decisions

- **VERIFY_SECRET vs reuse SESSION_SECRET.** Recommendation: dedicated
  secret. The session secret is rotated on operational events
  (suspected leak, scheduled rotation); each rotation should not invalidate
  a year's worth of verification signatures. Same reasoning as the
  existing `share_token_secret`.
- **Should the PDF embed the supervision event count?** Tempting (more
  detail) but exposes that "John did 47 missions but only one PDF" —
  a fingerprint. Keep the PDF aligned with the envelope: minimal fields
  only.
- **Should re-renders be rate-limited?** Yes, soft cap of 5 force-renders
  per submission per day. Prevents a user from cycling the cached PDF.
- **Rubric re-balance: re-issue envelopes or freeze them?** Freeze.
  Re-issuing changes hashes and breaks existing PDFs in the wild.

---

## P0-12. Reset-to-initial / clean session restart

### Goal

A user who has gone down a wrong path can wipe the workspace back to the
mission's initial commit **without** losing their supervision timeline.
Backtracking becomes a first-class affordance instead of an implicit
"abandon the session and restart" with full data loss. The reset is a
typed event so the grader, the post-mortem walkthrough (P0_DESIGN P0-2),
and any future replay tool can see how many times the user backed off.

### Architecture

The smallest item in this batch. No new tables, no new workers — one
endpoint, one dialog, one event type, one event-aware UI refresh.

```
[ apps/api/app/sessions/router.py ]
    + POST /sessions/{id}/reset
        – server runs `git reset --hard <initial_commit>` + `git clean -fd`
          in the sandbox
        – emits `session.reset` supervision event
        – inserts a FileChange row with source='revert'

[ apps/api/app/grading/score.py ]
    – Safety dimension surfaces `session.reset` count as a non-scoring
      signal (cap at 3 mentions in the strengths/weaknesses prose)
    – Diagnostics (P0_DESIGN P0-2) adds a critical_moment kind:
      "reset_then_repeated_same_mistake" — triggers when ≥2 resets occur
      and the post-reset edits hit the same paths the agent originally
      mis-edited

[ apps/web/components/workspace/WorkspaceTopBar.tsx ]
    + Reset workspace affordance, in an overflow menu next to "Submit"
      and "Give up" (P0_DESIGN P0-4)

[ apps/web/components/workspace/ResetWorkspaceDialog.tsx ]
    + Confirm dialog showing the reset count (no shame on the button
      itself, only inside the dialog)

[ apps/web/stores/workspaceStore.ts ]
    + On `session.reset` event from WS:
        invalidateQueries(['tree','diff','file'])
        clear fileBuffers for this session
        keep selectedContext (the user's intent stays)
```

### Data model (migration 0018)

No new columns. The existing `supervision_events` table accepts arbitrary
event_types via its JSONB payload. The migration ships two things:

```sql
-- 1) An index that the score engine + post-mortem use to count resets
--    cheaply per session.
CREATE INDEX idx_events_session_reset
    ON supervision_events (session_id)
    WHERE event_type = 'session.reset';

-- 2) A comment on the supervision_events table referencing the
--    canonical event type catalogue.
COMMENT ON TABLE supervision_events IS
    'Append-only log. Canonical event_type catalogue lives in docs/schemas/event.schema.json';
```

The event schema at
[`docs/schemas/event.schema.json`](docs/schemas/event.schema.json) gains:

```json
{
  "event_type": "session.reset",
  "payload": {
    "type": "object",
    "required": ["files_discarded", "had_agent_patch", "seconds_into_session"],
    "properties": {
      "files_discarded": { "type": "integer", "minimum": 0 },
      "had_agent_patch": { "type": "boolean" },
      "seconds_into_session": { "type": "integer", "minimum": 0 }
    }
  }
}
```

### API surface

```
POST /api/v1/sessions/{session_id}/reset
  Auth: owner only.
  Preconditions:
    - session.status == 'active' (enforced by _require_mutable_session
      in sessions/router.py)
    - sandbox handle is alive (the existing _get_sandbox_handle 503)
  Side-effects:
    1. driver.run(handle, ['git', 'status', '--porcelain'])
       → count modified + untracked files (used for telemetry only)
    2. driver.run(handle, ['git', 'reset', '--hard', '<initial_commit>'])
    3. driver.run(handle, ['git', 'clean', '-fd'])
    4. Emit supervision_event session.reset with the computed payload.
    5. Insert a FileChange row with source='revert', path='*',
       hunk_count=N, added_lines=0, removed_lines=<sum>.
  Response: 200
    {
      files_reset: int,
      new_tree_sha: string,   // == mission.initial_commit
      reset_count: int        // total resets on this session so far
    }
  Errors:
    409 if session is not active
    409 if a patch-apply is in flight (same per-handle lock the existing
        apply-patch endpoint uses)
    500 if git reset fails (sandbox unhealthy)
```

The endpoint reuses the existing scaffolding from `sessions/router.py`:
`_require_owned_session`, `_require_mutable_session`, `_get_sandbox_handle`,
`EventEmitter`. The new logic is roughly 40 lines.

### Frontend surface

[`WorkspaceTopBar.tsx`](apps/web/components/workspace/WorkspaceTopBar.tsx)
gains an overflow menu (`⋯`) that holds:

```
[ Submit ▶ ]   [ ⋯ ]
                 │
                 ├─ Reset workspace        (P0-12, this item)
                 ├─ Give up & reveal       (P0-4)
                 └─ Help (?)               (FEATURE_GAPS P1-8)
```

Click `Reset workspace` opens
`apps/web/components/workspace/ResetWorkspaceDialog.tsx`:

```
┌────────────────────────────────────────────────────┐
│ // reset workspace                                 │
│                                                    │
│ Roll the files back to the mission's initial       │
│ commit. Your file edits and the agent's patches    │
│ will be discarded.                                 │
│                                                    │
│ Your supervision timeline stays — the grader will  │
│ see this as a reset event.                         │
│                                                    │
│ You've reset this session [ 0 ] times.             │
│                                                    │
│ [ Cancel ]                  [ Yes, reset ]         │
└────────────────────────────────────────────────────┘
```

The dialog only displays the count internally — the *button* in the top
bar is always neutral so the affordance feels free, not shameful.

On confirm:
1. POST `/sessions/{id}/reset`.
2. On 200, invalidate React Query caches: `['session', sessionId, 'tree']`,
   `['session', sessionId, 'diff']`, every `['file', sessionId, *]`.
3. Clear the per-file `fileBuffers` map in
   [`workspaceStore.ts`](apps/web/stores/workspaceStore.ts). Keep
   `selectedContext` (the user's intent persists across resets).
4. Toast: "Workspace reset to initial commit."
5. The Timeline component receives the `session.reset` event via the
   existing WS stream and renders it inline.

### Scoring / telemetry interactions

**Score impact:** zero in MVP. The reset is a free affordance.

Rationale: penalising resets discourages the very exploration behaviour
the platform is trying to teach. Prompt-quality is `max` over turns, so
resetting cannot game it. Verification, Agent Review, and Safety are all
keyed off events that resets do not erase. Final Correctness only cares
about the submitted diff. Diff Minimality is computed on the final diff
— a reset that produces a cleaner final diff helps the user
*correctly*, by removing churn that didn't belong.

If telemetry later shows a power-user reset-spam exploit (say, ≥5 resets
per submission becomes common in high scores), revisit. For MVP: ship
without a penalty and instrument the count.

**Critical-moment kind** (P0_DESIGN P0-2): the diagnostics module gets a
new `kind: "reset_then_repeated_same_mistake"` — fires when ≥2 resets
occurred and the post-reset edits hit the same paths the agent's first
patch mis-edited. Surfaces in the post-mortem as: "you reset twice but
went after the same wrong file each time — try `find-in-files` (P0-9) on
the failure-mode keyword."

**Telemetry events:**
- `session_reset_requested` (from the dialog Confirm click — payload:
  `files_discarded_estimate`)
- `session_reset_completed` (from the API 200 — payload: `reset_count`)

### Edge cases

- **Reset during agent-patch apply.** The apply-patch endpoint already
  holds a per-handle lock in the sandbox pool. Reset request 409s with
  "patch apply in flight; retry in a moment." FE handles it as a toast.
- **Reset during submit.** Blocked by `_require_mutable_session` —
  session is `submitting`, not `active`.
- **`git reset` fails because the working tree has uncommitted binary
  files outside the index.** `git clean -fd` covers it (`-d` includes
  untracked directories). If both still fail, 500 with structured
  log + error toast.
- **The initial_commit on the mission was force-pushed after the session
  started** (shouldn't happen — initial_commit is pinned in
  `mission.yaml` — but defensive). `git reset --hard <sha>` fails. 500
  with detail "mission base commit no longer reachable; please open a
  fresh session." Logged as a SEV2 because pinned commits should never
  disappear.
- **User has an open file in Monaco when reset fires.** The query
  invalidation re-fetches the file. If the file was created by the
  agent patch (i.e. doesn't exist post-reset), the FE shows a "file
  not found" state in the editor pane; the file tree drops the row;
  the user picks something else. Handled by existing CodeEditor 404
  branch.
- **Selected context paths reference files that no longer exist
  post-reset.** Selection persists (it's the user's intent). The next
  prompt with stale paths is silently filtered server-side by the
  agent service. No new error path.
- **User clicks reset 10 times in 30 seconds.** Each succeeds and emits
  an event. The Timeline coalesces consecutive resets within 5s into
  one row (`Timeline.tsx` already groups events; extend its grouping
  rule for `session.reset`).
- **Reset on a session that has never had an agent patch applied.**
  Still works (clean reset to initial), `had_agent_patch: false` in
  payload. Harmless.

### Testing

- Pytest `test_reset_endpoint_happy_path.py` — apply mock patch, reset,
  assert tree sha + event emitted.
- Pytest `test_reset_requires_active_session.py` — 409 on
  submitting/graded/abandoned.
- Pytest `test_reset_event_payload.py` — payload shape matches schema.
- Pytest `test_reset_critical_moment_diagnostic.py` — given a fixture
  event stream with two resets that hit the same paths, the
  `compute_critical_moments` (P0_DESIGN P0-2) function emits one
  `reset_then_repeated_same_mistake` moment.
- Vitest `reset-dialog.test.tsx` — count rendering, cancel/confirm
  paths.
- Vitest `workspace-store-reset.test.ts` — store clears `fileBuffers`
  on the `session.reset` event but preserves `selectedContext`.
- Playwright `reset-workspace.spec.ts` — apply agent patch → reset →
  tree returns to initial → timeline shows reset event → another
  prompt-and-apply cycle works normally.

### Rollout

Single PR. No dependencies on other P0s in this batch. Migration 0018
is forward-only and idempotent (CREATE INDEX IF NOT EXISTS is safe; the
COMMENT is metadata only).

### Open decisions

- **Soft reset (revert agent patch only) as a second affordance?**
  Defer. Cognitive model is harder to explain ("which patches will be
  reverted?"). One button is the right starting point; add granular
  reverts only if user feedback asks for them.
- **Should reset clear the agent chat history?** No — the prompts are
  the supervision artefact and remain useful context for the user.
  Resetting the chat would erase the very thing the platform is
  measuring.
- **Should reset increment `attempt_index` (P0_DESIGN P0-3)?** No.
  Attempt index counts graded sessions, not resets within a session.
  A reset is a backtrack *within* an attempt.

---

## P0-13. Repo-level legal/license + contributor sanity

### Goal

The repo's posture matches reality. Today the project is public on GitHub
with no LICENSE (legally "all rights reserved"), no CONTRIBUTING.md, no
SECURITY.md, no PR/issue templates, and an
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) whose rubric weights
table disagrees with the runtime
[`apps/api/app/grading/dimensions.py`](apps/api/app/grading/dimensions.py).
Fixing this is cheap, mostly documentation, and load-bearing for trust —
OSS contributors will not file a PR against a license-less repo, and a
new contributor's first view of the codebase is the GitHub repo home,
not `docs/onboarding.md`.

### Scope

Three artefact groups, no code changes (except the invariant test in §C):

1. **LICENSE** at the repo root.
2. **CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md** at the repo
   root; `.github/` templates (PR + issue).
3. **Reconcile IMPLEMENTATION_PLAN.md §11 rubric tables with the
   shipped code**, plus a Pytest invariant that prevents the drift
   from recurring.

### A. License choice

Cannot be implemented without a team decision. Options, in order of
permissiveness:

| Option | Pros | Cons |
|---|---|---|
| **MIT** | Maximum reuse; trivially short text. | No patent grant. A competitor can fork + host with no give-back. |
| **Apache 2.0** | Explicit patent grant; standard for OSS dev tools; protects contributors from each other's patent claims. | ~11 KB; slightly heavier than MIT. |
| **MPL 2.0** | File-level copyleft; combines well with permissive deps. | Less familiar; some companies' legal teams reject MPL. |
| **AGPL v3** | Strong copyleft — any hosted modification must be open-sourced. Protects against extractive cloud forks. | Friction-heavy; deters corporate use; signals "we expect adversarial forks." |
| **BUSL → Apache 2.0** (Sentry/HashiCorp model) | Source-available with a "competing service" carve-out that lifts after N years. | Polarising; risks alienating OSS contributors. |
| **Source-available (custom)** | Pin the README's existing "Internal MVP — not for redistribution" framing. | Not OSS; closes contributor pipeline; "public repo but not OSS" confuses newcomers. |

**Recommendation: Apache 2.0.** Rationale:

- The patent grant matters — the supervisor rubric, failure-mode
  taxonomy, and grader determinism strategy are all plausibly
  patentable IP. Explicit grant prevents future legal grief between
  contributors.
- It signals "we want contributors" without the moral baggage of BUSL's
  "we'll switch licenses when convenient."
- AGPL's adversarial framing doesn't match a project whose goal is
  community-built mission content.

If the team disagrees and wants source-available, the LICENSE file
should be `LICENSE-CUSTOM` with explicit grant language ("for personal
and educational use; no commercial redistribution") and the README's
"Internal MVP" line stays.

### B. Files to ship

#### `LICENSE`

Apache 2.0 text from [apache.org/licenses/LICENSE-2.0.txt](https://www.apache.org/licenses/LICENSE-2.0.txt).
Add a copyright header inside the standard appendix:

```
Copyright 2026 OpenAgentDojo authors. See AUTHORS file.

Licensed under the Apache License, Version 2.0 (the "License");
...
```

An `AUTHORS` file at the root is optional; for a small team, omit it and
let `git log` be the authoritative list.

#### `CONTRIBUTING.md`

Eight sections, ~250 lines total. Outline:

1. **Welcome.** Two paragraphs. The project's pedagogical goal +
   pointer to [CONTEXT.md](CONTEXT.md). State that the highest-leverage
   contribution is a new mission.
2. **Setup in 30 seconds.** Inline command block. Pointer to
   [docs/onboarding.md](docs/onboarding.md) for the deep version.
3. **The high-leverage contribution: a new mission.**
   - Step 1: Open a `[scenario-proposal]` issue using the template at
     `.github/ISSUE_TEMPLATE/scenario-proposal.md`. Get design sign-off
     before writing the manifest.
   - Step 2: Copy [`docs/scenarios/template.md`](docs/scenarios/template.md)
     to `docs/scenarios/<NN>-<id>.md` and fill in the brief.
   - Step 3: Create `missions/<NN>-<id>/` with all required files (link
     to IMPLEMENTATION_PLAN.md §29.1 checklist).
   - Step 4: `pnpm validate:missions && cd apps/api && uv run pytest tests/missions`.
   - Step 5: PR includes the design note and a green CI.
4. **Bug fixes.** Regression test required. Conventional Commits.
5. **New validators.** Pointer to `apps/api/app/grading/validators/` for
   the pattern. Determinism rule.
6. **UI changes.** Pointer to the dojo aesthetic — open a
   `[design-proposal]` issue first so visual coherence stays.
7. **What we will not merge** (sets expectations early):
   - LLM calls on the grading hot path (violates ADR
     [0002-deterministic-agent.md](docs/adr/0002-deterministic-agent.md)).
   - Mission content that requires real network in the sandbox
     (sandboxes are `--network=none`).
   - Frontend deps that add ≥50 KB gzipped to the bundle without
     justification.
   - PRs without tests.
   - Breaking changes to the supervision-event schema without an ADR.
8. **Commit and PR conventions.**
   - Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`,
     `refactor:`, `test:`, `perf:`).
   - PRs reference an issue.
   - Squash-merge is the default.
   - DCO (`git commit -s`) is required — see [§D](#d-dco-vs-cla) below.
9. **Reporting security issues.** Pointer to `SECURITY.md` —
   responsible disclosure, 90-day window.

#### `CODE_OF_CONDUCT.md`

[Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
Boilerplate. Enforcement contact: a maintainer's real email.

#### `SECURITY.md`

```markdown
# Security policy

If you believe you have found a security vulnerability in OpenAgentDojo,
please **do not open a public issue**. Instead, email
**security@<domain>** with:

- A description of the vulnerability.
- Reproduction steps (URL, payload, expected vs actual behaviour).
- Your assessment of impact.

We will acknowledge receipt within 2 business days, share a remediation
timeline within 5 business days, and disclose publicly (with credit, if
you wish) after the fix has shipped — within 90 days, or earlier if the
fix is already deployed.

## In scope
- Sandbox isolation (escape, data leak across sessions).
- Authentication and session handling (magic links, cookies, CSRF).
- Authorisation (cross-user data access).
- Bedrock bearer-token handling.

## Out of scope
- Reports of rate-limit values being "too high"/"too low" without a
  concrete abuse vector.
- Self-XSS that requires the victim to paste hostile content into
  DevTools.
- DDoS at the IP layer (we rely on Cloudflare/Fly).

See [docs/security.md](docs/security.md) for the full security posture.
```

#### `.github/` templates

`.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## What and why
<!-- One paragraph. What changes, what user-visible behaviour shifts. -->

## How
<!-- Brief — point at the load-bearing files. -->

## Checklist
- [ ] Tests added/updated
- [ ] Determinism preserved (no `time.time()` / un-seeded `random` in
      graded code paths)
- [ ] Telemetry events added for any new user actions
- [ ] Migrations include both upgrade and downgrade
- [ ] Mission content touched? Ran `pnpm validate:missions` locally
- [ ] API surface changed? Regenerated `packages/shared-types/`
- [ ] OpenAPI compiles cleanly
```

`.github/ISSUE_TEMPLATE/bug.md` — repro steps, expected, actual,
environment.

`.github/ISSUE_TEMPLATE/scenario-proposal.md` — failure mode being
exercised, target difficulty, expected_context shape, ~3-line agent
patch outline, hidden-test sketch.

`.github/ISSUE_TEMPLATE/design-proposal.md` — affected surface,
visual/UX intent, why now.

`.github/ISSUE_TEMPLATE/config.yml` — link to `SECURITY.md` for
security issues so they don't land in the public bug tracker.

#### README.md edits

- Remove "License: Internal MVP — not for redistribution."
- Replace with: `Released under the [Apache License 2.0](LICENSE).`
- Add a small section under Quickstart:
  `Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md).`
- Status badge (currently "MVP complete") can stay; the conflict
  resolves once the license is clear.

### C. Reconciling rubric drift

The IMPLEMENTATION_PLAN.md §11.1 weights table lists:

| Dimension | IMPLEMENTATION_PLAN.md (stale) | Code (truth) |
|---|---|---|
| Verification Discipline | 20 | **15** |
| Diff Minimality | 5 | **10** |

The shipped code at
[`apps/api/app/grading/dimensions.py`](apps/api/app/grading/dimensions.py)
ships verification=15 and diff_minimality=10. Mission YAMLs and the
mission JSON schema both pin these via `const` so drift is structurally
prevented at the mission level — the plan is the only stale surface.

The §11.2 sub-scoring rules also need refresh — the actual scorers in
[`apps/api/app/grading/score.py`](apps/api/app/grading/score.py) award:

**Verification (cap 15)** — verified against
`_score_verification`:
- +6 targeted-test ran (with engagement-after-failure split: +6 if
  passing or follow-up edit/prompt, +3 if failing-and-ignored)
- +3 typecheck ran
- +2 lint ran
- +4 new regression test in the final diff
- −6 if zero verification commands

**Diff Minimality (cap 10)** — verified against
`_score_diff_minimality`, which uses `max(added, removed)` (not just
added) as the churn baseline:
- ratio ≤ 1.0 → 10
- 1.0 < ratio ≤ 1.5 → 8
- 1.5 < ratio ≤ 2.0 → 6
- 2.0 < ratio ≤ 3.0 → 4
- ratio > 3.0 → 0
- churn = 0 → 0 (empty submissions get no minimality credit)

The PR that reconciles the plan must:
1. Update §11.1 table with the correct weights.
2. Update §11.2.2 (Verification sub-scoring) with the actual signal
   weights from `_score_verification`.
3. Update §11.2.7 (Diff Minimality) with the actual scale + the
   `max(added, removed)` semantics + the churn=0 zero-floor.
4. Add a one-paragraph footnote: "The original weights in this section
   were rebalanced during M5 calibration; `apps/api/app/grading/dimensions.py`
   is the single source of truth. See ADR 0011 for rationale."
5. Add a new ADR
   [`docs/adr/0011-rubric-rebalance.md`](docs/adr/0011-rubric-rebalance.md)
   documenting why verification dropped from 20→15 and diff_minimality
   rose from 5→10 — the calibration data is in the existing
   `missions/_calibration/` envelopes.

#### Invariant test (anti-drift)

A new Pytest in `apps/api/tests/test_implementation_plan_rubric_invariant.py`:

```python
"""Anti-drift check: IMPLEMENTATION_PLAN.md §11.1 must match dimensions.py.

When the rubric is re-balanced, both surfaces must move together.
"""

import re
from pathlib import Path
from app.grading.dimensions import RUBRIC_DIMENSIONS

PLAN = Path(__file__).resolve().parents[3] / "IMPLEMENTATION_PLAN.md"
TABLE_HEAD = "### 11.1 Weight table"
ROW_RE = re.compile(r"\|\s*([A-Za-z ]+?)\s*\|\s*(\d+)\s*\|")

DIMENSION_LABELS = {
    "Final Patch Correctness": "final_correctness",
    "Verification Discipline":  "verification",
    "Agent Output Review":      "agent_review",
    "Prompt Quality":           "prompt_quality",
    "Context Selection":        "context_selection",
    "Safety Awareness":         "safety",
    "Diff Minimality":          "diff_minimality",
}

def test_plan_rubric_matches_dimensions_table():
    text = PLAN.read_text(encoding="utf-8")
    section = text.split(TABLE_HEAD, 1)[1].split("###", 1)[0]
    plan = {DIMENSION_LABELS[label]: int(maxn)
            for label, maxn in ROW_RE.findall(section)
            if label.strip() in DIMENSION_LABELS}
    assert plan == dict(RUBRIC_DIMENSIONS), (
        f"IMPLEMENTATION_PLAN §11.1 disagrees with dimensions.py:\n"
        f"  plan:    {plan}\n"
        f"  shipped: {dict(RUBRIC_DIMENSIONS)}"
    )
```

The test fails immediately if either side moves without the other.
Catches the entire class of "doc lies about code" bugs.

### D. DCO vs CLA

Apache 2.0 implies a license grant per contribution. Two ways to capture
it:

- **Contributor License Agreement (CLA).** A document each contributor
  signs (manually or via a bot like CLA Assistant). Heavyweight,
  unfriendly for one-line-doc contributors.
- **Developer Certificate of Origin (DCO).** Each commit carries
  `Signed-off-by: Name <email>` (`git commit -s` enforces it). The
  contributor implicitly affirms the DCO text per commit. Used by the
  Linux kernel, Docker, GitLab, and most modern OSS.

**Recommendation: DCO.** Lower friction, sufficient for Apache 2.0,
enforceable by a GitHub Action (`probot/dco` or the DCO check shipped
with GitHub Actions). CONTRIBUTING.md §8 lists the requirement; CI
fails PRs without sign-off.

### Edge cases

- **Existing contributors object to the license choice.** Handle in a
  GitHub Discussion before merging LICENSE. Each existing contributor
  per `git log` must explicitly agree (or be the assumed legal author —
  the codebase is small enough that this is tractable).
- **Apache 2.0 + Bedrock terms.** AWS's Bedrock TOS does not restrict
  redistribution of code that *calls* Bedrock. The bearer token is the
  user's credential, not the code's. No conflict.
- **Apache 2.0 + downstream re-license.** A downstream user can fork
  and re-license under any compatible license (including proprietary).
  This is the cost of Apache 2.0 and the recommended trade-off; if it
  is unacceptable, switch the recommendation to AGPL.
- **DCO + anonymous contributors.** GitHub accounts that use
  `noreply.github.com` emails can still sign off — the DCO requires an
  identity, not necessarily a real email.

### Testing

- Pytest `test_implementation_plan_rubric_invariant.py` (see §C above).
- CI gate: a GitHub Action that runs the DCO check on every PR.
- Markdownlint on `CONTRIBUTING.md` / `SECURITY.md` /
  `CODE_OF_CONDUCT.md` to catch broken links and bad headings.
- A simple link-checker job (`lychee` or `markdown-link-check`) over
  the root markdown so links into `docs/` keep working as paths change.

### Rollout

Four PRs that can land in any order:

1. **PR1 — LICENSE + README update.** Unblocks contribution. Smallest
   PR; should land first.
2. **PR2 — CONTRIBUTING.md + CODE_OF_CONDUCT.md + SECURITY.md.** Sets
   expectations for incoming contributors.
3. **PR3 — `.github/` templates + DCO CI gate.** Operational. Depends
   on the DCO decision in §D.
4. **PR4 — Rubric reconciliation: IMPLEMENTATION_PLAN.md §11 update +
   ADR 0011 + invariant Pytest.** Independent of the others; pure docs
   + one test.

Each PR is reviewable independently and adds value the moment it lands.

### Open decisions

- **License choice.** Apache 2.0 recommended; team must confirm before
  PR1 ships.
- **DCO vs CLA.** DCO recommended.
- **Maintainer email for security@ and conduct@.** Real address
  required; cannot ship `SECURITY.md` with a placeholder.
- **Should the rubric ADR be a new file (0011) or an addendum to
  ADR 0006?** New file. Addenda are easier to miss; a dated ADR is the
  honest historical record of "we re-balanced and here is why."
- **Should `pnpm validate:missions` be expanded to also lint
  `docs/scenarios/*.md` for the design-note template fields?**
  Defer — current scope of P0-13 is repo hygiene, not content tooling.

---

## A. What stays the same

The architectural invariants from P0_DESIGN.md §B continue to hold for
this batch:

- **Determinism on the grading path.** P0-11's verification hash is a
  pure function of the envelope; identical inputs produce identical
  hashes across replays. P0-12's reset emits an event but does not
  influence the rubric. P0-13 is documentation-only.
- **Event-sourcing.** P0-12 adds `session.reset` to the canonical
  event catalogue. P0-11 adds telemetry-only events (no
  supervision_events impact).
- **Mission manifest as content contract.** Nothing in this batch
  changes the manifest schema. P0-11 adds `mission_version` reads
  from the existing manifest field.
- **Sandbox isolation.** P0-12 runs `git reset` inside the sandbox via
  the existing `driver.run` boundary — no new container privileges.
- **Process-only score preview during sessions.** P0-11 only touches
  post-submit artefacts. P0-12 adds an in-session affordance but does
  not surface scoring information.

— design authored against branch `codex/goal`.
