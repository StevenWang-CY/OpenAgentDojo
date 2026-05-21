# API Reference

This document is a human-readable reference for the FastAPI surface defined in [IMPLEMENTATION_PLAN.md §12](../IMPLEMENTATION_PLAN.md). The authoritative contract is the generated `/openapi.json` from the running API; this doc summarizes shapes and conventions so the average new endpoint doesn't require re-reading the spec.

## Conventions

- **Base path:** all REST routes live under `/api/v1`. The version segment is reserved for breaking changes; additive changes do not bump it.
- **Auth:** session cookie (`arena_session`, HttpOnly, Secure, SameSite=Lax). All `POST`, `PUT`, `DELETE` require an `X-Csrf-Token` header matching the per-session token returned by `GET /me`.
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

### `POST /api/v1/auth/logout`
Clears the cookie and invalidates the server-side session.

- 204 on success.
- Requires auth.

### `GET /api/v1/me`
Returns the current user plus a fresh CSRF token.

- 200 `{ "user": User, "csrf_token": string }`.
- 401 if unauthenticated.

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

- Body: `{ "mission_id": "string" }`.
- 201 `Session` (status=`provisioning`).
- 400 `mission_not_found`; 409 `active_session_exists` (per-user cap of 1 active session); 429 `rate_limited`.
- Auth required.

### `GET /api/v1/sessions/{id}`
- 200 `SessionDetail`. Includes status, sandbox metadata, current commit, and counts (`agent_turns`, `command_runs`).
- 404 `session_not_found`; 403 `not_session_owner`.

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

### `GET /api/v1/sessions/{id}/diff`
- 200 `{ "unified_diff": "string" }`. Diff is `current_commit` vs `initial_commit`.

### `GET /api/v1/sessions/{id}/timeline`
- 200 `SupervisionEvent[]` ordered by `occurred_at`. Useful for replay and debugging. The live channel is `/ws/sessions/{id}/events`.

### `POST /api/v1/sessions/{id}/submit`
Freeze the sandbox, run hidden tests + validators, compute the score.

- 202 Accepted with `{ "submission_id": "string" }`. Polling `/sessions/{id}/submission` returns the final result.
- 409 `already_submitted`.

### `GET /api/v1/sessions/{id}/submission`
- 200 `Submission { id, status, score_report, ... }` once grading completes; otherwise 425 Too Early.

## Submissions & Reports

### `GET /api/v1/reports/{submission_id}`
- 200 `Submission` with the full `score_report` per [docs/schemas/score_report.schema.json](./schemas/score_report.schema.json). Includes the ideal-solution markdown.
- 403 `not_report_owner` unless the report is publicly shared.

## Profile

### `GET /api/v1/profiles/{user_id}`
Public profile. **No auth required.**

- 200 `PublicProfile { handle, display_name, badges, mission_history, radar_averages }`.
- `mission_history` includes only published submissions (the user can opt out per submission post-MVP).

## WebSocket channels

Both channels authenticate via a short-lived signed token issued by `GET /me` (`?token=…` query parameter). Tokens expire after 60 seconds; the client reconnects with a fresh token on disconnect.

### `/ws/sessions/{id}/terminal`
Bidirectional PTY stream.

- **Client → server frames:**
  - `{ "type": "input", "data": "string" }` — keystrokes.
  - `{ "type": "resize", "cols": number, "rows": number }` — TTY resize.
- **Server → client frames:**
  - `{ "type": "output", "data": "string" }` — stdout/stderr chunks.
  - `{ "type": "exit", "code": number }` — when the active command exits.

Frames are JSON. Binary (xterm raw mode) is supported via a `Sec-WebSocket-Protocol: arena.binary.v1` upgrade but defaults off for MVP.

### `/ws/sessions/{id}/events`
Server-only stream of supervision events as they happen.

- Frames match [docs/schemas/event.schema.json](./schemas/event.schema.json) (`event_type`, `payload`, `occurred_at`).
- The frontend feeds these into the workspace store; `ScorePreview` recomputes its partial signals on each frame.
- The channel is read-only; client frames are ignored (heartbeat handled by WebSocket pings).

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

- `GET /healthz` — liveness; returns `{ "ok": true, "version": "string" }`.
- `GET /readyz` — readiness; checks Postgres, Redis, and the sandbox driver.
- `GET /metrics` — Prometheus exposition. Behind an allowlist in prod.

## Versioning policy

- **Additive changes** (new endpoint, new optional field, new enum value at the end) — non-breaking, ship freely.
- **Breaking changes** (rename field, remove field, narrow types) — require a `/api/v2` cutover. The platform supports two major versions in parallel for one full milestone before retiring the older one.
- Removed fields are deprecated for one full milestone before deletion. `Deprecation` and `Sunset` HTTP headers communicate the timeline.
