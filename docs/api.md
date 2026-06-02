# API Reference

This document is a human-readable reference for the FastAPI surface defined in [IMPLEMENTATION_PLAN.md §12](../IMPLEMENTATION_PLAN.md). The authoritative contract is the generated `/openapi.json` from the running API; this doc summarizes shapes and conventions so the average new endpoint doesn't require re-reading the spec.

## Conventions

- **Base path:** all REST routes live under `/api/v1`. The version segment is reserved for breaking changes; additive changes do not bump it.
- **Auth:** session cookie (`arena_session`, HttpOnly, Secure, SameSite=Lax). CSRF is a double-submit cookie: the non-HttpOnly `arena_csrf` cookie is echoed back in an `X-Csrf-Token` header on all unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`). The token is returned by `GET /api/v1/auth/me`.
- **Content type:** request and response bodies are JSON unless otherwise stated. Timestamps are ISO-8601 UTC. UUIDs are lowercase canonical.
- **Errors:** plain `HTTPException` responses use FastAPI's default envelope `{ "detail": "string" }`. Structured failures raised as `ArenaError` (see `apps/api/app/main.py`) extend that envelope with a stable, machine-parsable code:
  ```json
  { "detail": "human-readable message", "code": "stable_code" }
  ```
  Unhandled exceptions are logged server-side and surface as `{ "detail": "internal server error", "code": "internal_error" }` with HTTP 500.
- **Pagination:** list endpoints accept `limit` (default 50, max 200) and `cursor` (opaque string). Responses include `next_cursor` (null when exhausted).
- **Rate limits:** documented per resource below; the platform returns `429 Too Many Requests` with `Retry-After` in seconds. See [IMPLEMENTATION_PLAN.md §21](../IMPLEMENTATION_PLAN.md).

## Auth

### `POST /api/v1/auth/magic-link`
Send a single-use sign-in link to an email.

- Body: `{ "email": "string" }`
- 204 No Content on success.
- 400 `invalid_email`; 429 `rate_limited`.
- Anonymous.

### `GET /api/v1/auth/callback?token=…`
Magic-link landing. Validates the JWT, sets the session cookie, redirects to `/missions`.

- 302 on success.
- 400 `expired_or_used_token`.

### `POST /api/v1/auth/magic-link/resend`
Re-send the most recent magic link to an email, subject to a cooldown. 200 `{ "wait_seconds": number }` (the cooldown remaining before another resend is allowed). Anonymous.

### `POST /api/v1/auth/logout`
Clears the cookie and invalidates the server-side session.

- 204 on success.
- Requires auth.

### `GET /api/v1/auth/me`
Returns the current user plus a fresh CSRF token. This is the canonical route; the legacy top-level `/api/v1/me` alias was retired.

- 200 `{ "user": User, "csrf_token": string }`.
- 401 if unauthenticated.

### `POST /api/v1/auth/csrf-refresh`
Force-rotate the CSRF cookie. Useful for tooling that wants a fresh token without going through `/auth/callback`. 200 with the same shape as `/auth/me`. Requires auth.

### GitHub OAuth (P0-7)

GitHub sign-in/linking is shipped (gated behind the GitHub app credentials).

- `GET /api/v1/auth/github/available` — `{ "enabled": boolean }`; whether GitHub OAuth is configured. Anonymous.
- `GET /api/v1/auth/github/start` — begin the OAuth dance; redirects to GitHub.
- `GET /api/v1/auth/github/callback` — OAuth landing; exchanges the code, sets the session cookie.

## Account self-service (P0-6)

All require auth + CSRF and emit a row into `account_events` (separate table from `supervision_events`).

### `PATCH /api/v1/auth/me`
Update mutable profile fields (`display_name`, `handle`). 200 `User`.

### `POST /api/v1/auth/me/email/change`
Start an email-change flow. Stores `users.pending_email` and emails a confirmation token. 204. Emits `account.email_change_requested`.

### `POST /api/v1/auth/me/email/confirm`
Confirm the new email with the token from the email. 204 on success. Emits `account.email_changed`.

### `POST /api/v1/auth/me/sessions/sign-out-all`
Force-rotate `users.session_epoch` so every cookie issued under the previous epoch is rejected. 204.

### `POST /api/v1/auth/me/data-export`
Enqueue a data-export job. 202 `DataExportRead`. 403 if account deletion is pending.

### `GET /api/v1/auth/me/data-export/{export_id}`
Poll one export. 200 `DataExportRead { id, status, requested_at, ready_at?, expires_at?, bytes_total?, download_url?, error? }` where `status` is one of `queued | running | ready | failed | expired`.

### `GET /api/v1/auth/me/data-export/latest`
Return the most recent export for the user. 200 `DataExportRead`, or 204 No Content when the user has never requested one.

### `POST /api/v1/auth/me/data-export/{export_id}/kick`
Nudge a stuck export — re-enqueues (or marks failed) a row that has been `queued`/`running` too long with no worker progress. 200 `DataExportRead`.

### `POST /api/v1/auth/me/delete`
Schedule account deletion with a grace window. 202. Emits `account.deletion_scheduled`. The cron at `scripts/process_deletion_grace.py` tombstones the row when the grace expires (emits `account.deleted`).

### `POST /api/v1/auth/me/delete/cancel`
Cancel a scheduled deletion. 204. Emits `account.deletion_cancelled`.

## Consent (P0-5)

### `GET /api/v1/auth/me/consent`
Returns the current consent state plus the active `CONSENT_POLICY_VERSION`.

### `POST /api/v1/auth/me/consent`
Update the per-kind consent. Emits `consent.granted` or `consent.revoked` with `{kind, version}`. 204.

## Coaching consent (P1-4)

### `GET /api/v1/auth/me/coaching-consent`
Returns whether the user has opted into LLM-backed coaching reflections.

### `POST /api/v1/auth/me/coaching-consent`
Update the coaching-consent flag.

## Tutorial (P0-1)

### `POST /api/v1/auth/me/tutorial/replay`
Clear `tutorial_completed_at` + increment `tutorial_replay_count`. 200 `User`.

## Missions

### `GET /api/v1/missions`
Catalog of published missions.

- Query: `category?`, `difficulty?`, `limit?`, `cursor?`.
- 200 `{ "items": Mission[], "next_cursor": string|null }`.
- Anonymous.

### `GET /api/v1/missions/{id}`
Mission details for the catalog/detail page. **Does not** include hidden tests or ideal solution.

- 200 `MissionDetail`.
- 404 `mission_not_found`.

`Mission` shape (catalog):
```json
{
  "id": "auth-cookie-expiration",
  "title": "Expired Session Cookie Still Grants Access",
  "short_description": "…",
  "difficulty": "intermediate",
  "category": "auth",
  "estimated_minutes": 35,
  "skills_tested": ["auth", "security"],
  "failure_mode": { "id": "checks_presence_not_expiration", "title": "…" }
}
```

`MissionDetail` adds `brief` (markdown), `visible_tests`, and `expected_files`. Hidden information stays server-side.

## Sessions

### `POST /api/v1/sessions`
Provision a new session for a mission.

- Body: `{ "mission_id": "string", "mode"?: "self_study"|"proctored", "previous_session_id"?: "uuid" }`. `mode` defaults to `self_study`; `proctored` (P0-8) enables integrity-signal collection.
- 202 Accepted, `Session` (status=`provisioning`).
- 400 `mission_not_found`; 409 `active_session_exists` (per-user cap of 1 active session); 429 `rate_limited`.
- Auth required.

### `GET /api/v1/sessions/{id}`
- 200 `SessionDetail`. Includes status, sandbox metadata, current commit, and counts (`agent_turns`, `command_runs`).
- 404 `session_not_found`; 403 `not_session_owner`.

### `GET /api/v1/sessions/{id}/ws-token`
Mint a short-lived signed token for the WebSocket channels. Owner-only.

- 200 `{ "token": string, "ttl_seconds": 60 }`. The token is bound to the session and the user's `session_epoch`; the client passes it as the `?token=…` query parameter when opening `/ws/...` and reconnects with a fresh token on disconnect.

### `POST /api/v1/sessions/{id}/context`
Record the user's context selection for the current turn. Emits `context.selected`.

- Body: `{ "files": string[], "logs"?: string[], "tests"?: string[], "extras"?: string[] }`.
- 204.
- 400 `invalid_paths`.

### `POST /api/v1/sessions/{id}/prompts`
Submit a prompt to the agent. Emits `prompt.submitted`, then `agent.responded` once the (deterministic) agent service returns.

- Body: `{ "text": "string", "context_id"?: "string" }`.
- 201 `AgentTurn` with `{ id, turn_index, agent_response, proposed_actions: ["apply_patch"]? }`.
- 400 `prompt_too_short`; 429 `prompt_rate_limited`.

### `POST /api/v1/sessions/{id}/patches/{turn_id}/apply`
Apply the agent's pre-written patch to the sandbox. Emits `patch.applied`.

- 200 `{ "files_changed": number, "added": number, "removed": number }`.
- 409 `patch_already_applied`; 422 `patch_apply_failed` (rare; surfaces as a non-blocking error in the UI).

### `POST /api/v1/sessions/{id}/files`
User manual edit. Stores the new content and emits `file.edited`.

- Body: `{ "path": "string", "content": "string" }`.
- 204.
- 400 `path_outside_workspace`; 413 `file_too_large`.

### `POST /api/v1/sessions/{id}/files/revert`
Revert one file to the last applied agent state. Emits `file.reverted`.

- Body: `{ "path": "string" }`.
- 204.

### `POST /api/v1/sessions/{id}/commands`
Run a command in the sandbox. The terminal WebSocket is the streaming path; this endpoint is for headless callers (TestPanel buttons). Emits `command.run` with `category`.

- Body: `{ "command": "string", "category": "test"|"typecheck"|"lint"|"manual"|"other" }`.
- 200 `CommandRun { id, exit_code, duration_ms, stdout_s3_key, stderr_s3_key }`.
- 400 `banned_command`; 429 `command_rate_limited`.

### `GET /api/v1/sessions/{id}/tree`
Returns the current file tree of `/workspace`.

- 200 `FileTree { root, children }`.

### `GET /api/v1/sessions/{id}/file?path=…`
- 200 `{ "content": "string", "encoding": "utf-8"|"base64" }`.
- 404 `file_not_found`.

### `GET /api/v1/sessions/{id}/files/list` (P0-9)
Quick, gitignore-aware listing of workspace files, fuzzy-filtered server-side. Backs the command-palette / file-open picker.

- Query: `q?` (fuzzy filter), `limit?`.
- 200 with the matching file paths.

### `POST /api/v1/sessions/{id}/files/search` (P0-9)
Repo-wide find-in-files, ripgrep-backed.

- Body: `{ "query": "string", "regex"?: boolean, "case_sensitive"?: boolean, "glob"?: "string", "max_results"?: number }`.
- 200 with the ranked matches (file, line, snippet).

### `GET /api/v1/sessions/{id}/diff`
- 200 `{ "unified_diff": "string" }`. Diff is `current_commit` vs `initial_commit`.

### `GET /api/v1/sessions/{id}/timeline`
- 200 `SupervisionEvent[]` ordered by `occurred_at`. Useful for replay and debugging. The live channel is `/ws/sessions/{id}/events`.

### `GET /api/v1/sessions/{id}/note` · `PUT /api/v1/sessions/{id}/note` (P1-4)
The per-session scratchpad. `GET` returns the current note; `PUT` upserts it (body `{ "body": "string" }`). Both 200.

### `POST /api/v1/sessions/{id}/events/note-viewed` (P1-4)
Record that the user focused the prompt composer while the scratchpad had content. Body `{ "bytes_at_view": number }`. 204.

### `POST /api/v1/sessions/{id}/events/diff-opened`
Record that the user opened the diff viewer (feeds scoring + badges). Optional body `{ "path"?: "string" }`. 204.

### `POST /api/v1/sessions/{id}/events/tutorial-step` (P0-1)
Record a tutorial coachmark step transition. Body `{ "step_id": "string", "action"?: "string" }`. 204.

### `POST /api/v1/sessions/{id}/events/integrity` (P0-8)
Proctored-mode only: record an integrity signal (blur, paste, devtools, etc.). Body `{ "kind": "string", "payload"?: object }`. 204.

### `POST /api/v1/sessions/{id}/submit`
Freeze the sandbox, run hidden tests + validators, compute the score.

- 202 Accepted with `{ "submission_id": "string" }`. Polling `/sessions/{id}/submission` returns the final result.
- 409 `already_submitted`.

### `GET /api/v1/sessions/{id}/submission`
- 200 `Submission { id, status, score_report, ... }` once grading completes; otherwise 425 Too Early.

### `POST /api/v1/sessions/{id}/give-up` (P0-4)
Forfeit the active session: reveal the ideal solution and persist a graded submission with the score capped at 50. Gated behind a minimum time-in-session (`give_up_min_seconds`).

- 200 `Submission`.
- Returns a structured error (`give_up_not_yet_available`, `give_up_not_supported_for_tutorial`, or a status mismatch) when the forfeit is not allowed.

### `POST /api/v1/sessions/{id}/reset` (P0-12)
Reset the workspace back to the mission's initial commit.

- 200 `{ "files_reset": number, "new_head_commit": string, "reset_count": number }`.

## Submissions & Reports

### `GET /api/v1/reports/{submission_id}`
- 200 `Submission` with the full `score_report` per [docs/schemas/score_report.schema.json](./schemas/score_report.schema.json). Includes the ideal-solution markdown, `ideal_solution_diff`, and the agent's original `agent_patch_diff` (P0-2). The diff payloads are gated on `session.status == 'graded'`.
- 403 `not_report_owner` unless the report is publicly shared.

### `POST /api/v1/reports/{submission_id}/share`
Owner-only: mint a 30-day signed share JWT (signed with `SHARE_TOKEN_SECRET`, see [docs/runbooks/rotate-secrets.md](./runbooks/rotate-secrets.md)). Returns `{ share_token, share_url, expires_at }`.

### `GET /api/v1/reports/{submission_id}/render?kind=pdf|png` (P0-11)
Owner-or-share endpoint. Lifecycle:
- row missing → enqueue + 202 with `poll_after_seconds`.
- queued / running → 202.
- ready → 302 to a 5-minute signed R2 URL.
- failed → 503 with the worker's error message.

### `POST /api/v1/reports/{submission_id}/render` (P0-11)
Owner-only: force re-render (`{ kind: "pdf" | "png" }`). 202 with the new row. Idempotent during queued / running. Rate-limited at `report_render_force_daily_cap` per submission per 24h (429 `force_render_rate_limited` when exceeded).

### `GET /api/v1/reports/{submission_id}/print`
Owner-or-share, server-rendered print view used as the source for PDF/PNG rendering.

### `GET /api/v1/submissions/{submission_id}/coaching` (P1-4)
LLM-backed coaching reflection for a graded submission. Owner-only and gated on coaching consent. 200 with the reflection; 401/403 when not authorised, 404 when the submission is missing/not graded, 503 when the reflection is unavailable.

### `GET /api/v1/submissions/{submission_id}/replay.json` · `replay.zip` (P1-6)
Deterministic replay artefact of a graded session. `replay.json` returns an `application/json` object; `replay.zip` returns the bundle as `application/zip`. 404 when the submission is missing/not graded; 503 when the artefact is not ready.

## Verification (P0-11)

### `GET /api/v1/verify/{submission_id}`
Anonymous, no auth. Returns the canonical verification envelope plus `canonical_url`, `verification_hash`, `verification_signature`. The hash is a SHA-256 over the canonical JSON of the envelope; the signature is HMAC-SHA256 of the hash under `VERIFY_SECRET`. Response is cacheable for one year (`Cache-Control: public, max-age=31536000, immutable`) and carries `X-Robots-Tag: index, follow` so the URL is the verification path a third party can Google.

404 when:
- the submission does not exist,
- the session is not yet graded,
- the mission is `kind=tutorial` (tutorials are not credentialing),
- `verification_hash` is NULL (older graded rows that predate the stamping path; operators run `scripts/backfill_verification.py` to re-stamp).

## Profile

### `GET /api/v1/profiles/{handle}`
Public profile, keyed by the user's **handle** (not user_id). **No auth required.**

- 200 `PublicProfile { handle, display_name?, joined_at, badges, history, radar_averages, dimension_trends, total_missions, best_score?, ... }` (plus GitHub-link fields when the profile is GitHub-verified).
- `history` includes only published submissions.

### `GET /api/v1/profiles/me/skills`
Authenticated: the caller's skills catalogue (failure-modes covered vs. total). 200 `SkillsCatalog`.

### `GET /api/v1/me/recommendations` (P1-2)
Authenticated: personalised next-mission recommendations.

- 200 `RecommendationSet { diagnosis, recommendations (0..3 items), computed_at, cache_hit, weakest_dim? }`.

## WebSocket channels

The WebSocket channels are not part of `openapi.json`. All three authenticate via a short-lived signed token minted by `GET /api/v1/sessions/{id}/ws-token` (passed as the `?token=…` query parameter). Tokens expire after 60 seconds; the client reconnects with a fresh token on disconnect.

### `/ws/sessions/{id}/terminal`
Bidirectional PTY stream.

- **Client → server frames:**
  - `{ "type": "input", "data": "string" }` — keystrokes.
  - `{ "type": "resize", "cols": number, "rows": number }` — TTY resize.
- **Server → client frames:**
  - `{ "type": "output", "data": "string" }` — stdout/stderr chunks.
  - `{ "type": "exit", "code": number }` — when the active command exits.
  - `{ "type": "error", "code": "string", "detail": "string" }` — control frame sent before an abnormal close (e.g. `no_sandbox`, `attach_failed`).

Frames are JSON. Binary (xterm raw mode) is supported via a `Sec-WebSocket-Protocol: arena.binary.v1` upgrade but defaults off for MVP.

### `/ws/sessions/{id}/events`
Server-only stream of supervision events as they happen.

- Frames match [docs/schemas/event.schema.json](./schemas/event.schema.json) (`event_type`, `payload`, `occurred_at`).
- The frontend feeds these into the workspace store; `ScorePreview` recomputes its partial signals on each frame.
- The channel is read-only; client frames are ignored (heartbeat handled by WebSocket pings).

### `/ws/sessions/{id}/lsp` (P1-3)
Language-server proxy. Forwards LSP traffic to a per-session language server for in-editor diagnostics, hover, and go-to-definition. Same ws-token auth as the other channels.

## OpenAPI & typed clients

FastAPI emits `/openapi.json` at runtime. The contract flow is:

1. `apps/api/app/main.py` mounts routers and tags each operation.
2. CI runs `uv --project apps/api run python apps/api/scripts/dump_openapi.py`, which writes the schema to `apps/api/openapi.json` (committed; the contracts workflow fails on drift).
3. CI runs `pnpm --filter @arena/shared-types regen`, which generates `packages/shared-types/src/api.gen.ts` from that JSON.
4. The frontend imports typed paths/components from `@arena/shared-types` — `api.gen.ts` for OpenAPI-derived shapes and the hand-curated `api.ts` / `events.ts` for higher-level wrappers and WebSocket event types.

The package layout is:

- `packages/shared-types/src/api.gen.ts` — generated from `apps/api/openapi.json` via `openapi-typescript`; never hand-edit. CI regenerates and `git diff --exit-code`s it (see `.github/workflows/contracts.yml`).
- `packages/shared-types/src/api.ts` — hand-curated re-exports, helper types, and the fetch wrapper that pins the contract to the generated types.
- `packages/shared-types/src/events.ts` — hand-curated supervision event union (`/ws/sessions/{id}/events`) shared between backend tests and the frontend timeline.

Adding a new endpoint: tag it in the FastAPI router, run `pnpm --filter @arena/shared-types regen` locally, and commit the regenerated `api.gen.ts` alongside the route change. CI will fail otherwise.

## Status & health

- `GET /healthz` — liveness; cheap (no DB/Redis touch). Returns `{ "status": "ok", "sandbox_driver": "string", "env": "string", "version": "string" }`.
- `GET /healthz/ready` — readiness; checks Postgres, Redis, and the sandbox driver (S3 is best-effort). Returns `{ "db": bool, "redis": bool, "s3": bool, "sandbox": bool, "sandbox_driver": "string", "version": "string" }` with HTTP 200, or the same body with HTTP 503 when DB/Redis/sandbox is unreachable.
- `GET /status` and `GET /api/v1/status` — public status pages.
- `GET /metrics` — Prometheus exposition. Behind an allowlist in prod.

## Versioning policy

- **Additive changes** (new endpoint, new optional field, new enum value at the end) — non-breaking, ship freely.
- **Breaking changes** (rename field, remove field, narrow types) — require a `/api/v2` cutover. The platform supports two major versions in parallel for one full milestone before retiring the older one.
- Removed fields are deprecated for one full milestone before deletion. `Deprecation` and `Sunset` HTTP headers communicate the timeline.
