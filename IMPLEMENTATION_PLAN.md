# OpenAgentDojo — Full Implementation Plan

> This document is the engineering source-of-truth for the MVP build. It expands the product definition into a buildable, milestone-driven plan with concrete file layouts, schemas, contracts, scoring logic, scenario data, and a build order a coding agent can execute end-to-end.

---

## 0. How to Use This Document

This plan is written for a coding agent (and human collaborators) to execute sequentially. Each section is self-contained enough that a fresh agent picking up midway can continue without reading the entire upstream context.

Conventions:

- **MUST / SHOULD / MAY** follow RFC 2119 semantics.
- File paths are repo-relative.
- All scenarios, scoring weights, and schemas are normative — change them only after updating this document.
- If you discover an inconsistency between this plan and the product definition (`OpenAgentDojo — Ambitious MVP Product Definition`), this plan wins for *implementation* questions; the product definition wins for *intent* questions.
- Determinism is a feature. The product MUST grade the same submission identically on every replay. Anything stochastic (LLM calls, network) goes through a seeded or replayable layer.

---

## 1. North-Star Summary

**Product:** OpenAgentDojo — a browser-based simulator where developers complete repository missions by supervising a deliberately-flawed coding agent.

**MVP must ship:**

1. 10 curated missions across distinct failure modes.
2. 2 controlled demo repositories (one fullstack TS/Node, one Python data-API).
3. Browser workspace: file tree, Monaco editor, xterm terminal, diff viewer, agent chat, mission brief, test panel, supervision timeline.
4. Built-in **hybrid-simulation agent** (deterministic scripted patches per scenario, optional LLM-narrated explanations).
5. Hidden-test grading engine + scenario-specific validators.
6. Process-based supervision scoring on a 100-point rubric.
7. Post-mission report + public skill profile.
8. Landing page strong enough for a recruiter cold-open.

**The single hardest design constraint:** *the platform grades the human's supervision process, not only the final patch*. Every system decision must preserve the audit trail (prompt, context selection, diff inspection, command runs, test runs, corrections) with high fidelity and replayability.

---

## 2. Tech Stack (Locked)

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 15 (App Router) + React 19 + TypeScript 5.6 | SSR for landing/profile; client-only for workspace |
| Styling | Tailwind 4 + shadcn/ui + Radix primitives | Fast, consistent, accessible |
| Editor | Monaco Editor (`@monaco-editor/react`) | Diff viewer + multi-language LSP-lite |
| Terminal | xterm.js + `xterm-addon-fit` + WebSocket | Real terminal feel |
| Diff UI | `react-diff-view` (gitdiff-parser) + Monaco diff mode | Side-by-side and unified |
| State | Zustand for workspace; React Query for server state | Avoid Redux ceremony |
| Backend | FastAPI (Python 3.12) | Pydantic models = single source of truth for schemas; great async story |
| DB | PostgreSQL 16 via SQLAlchemy 2.x (async) + Alembic | |
| Cache/Queue | Redis 7 + RQ (or Celery — pick RQ for MVP simplicity) | Sandbox-run jobs |
| Sandbox | Docker (rootless) with per-session ephemeral containers | Resource caps via cgroups |
| Realtime | WebSockets (FastAPI native) — terminal + timeline stream | |
| Auth | Email magic link (Resend) + session cookies; optional GitHub OAuth | No password storage |
| LLM (optional narration) | Claude `claude-haiku-4-5` via **Anthropic-on-Bedrock** (`AsyncAnthropicBedrock`, prompt-cached) — gated behind a feature flag; agent *patches* are deterministic regardless. See §16.A for env vars and the Civitas client helper. | |
| Object storage | S3-compatible (MinIO local; R2/S3 prod) for diff/log archives | |
| Infra (prod) | Fly.io or Render (web) + Fly Machines or AWS Fargate (sandboxes) | Single-region MVP |
| CI | GitHub Actions: typecheck, ruff/black, pytest, vitest, e2e (Playwright) | |
| Tracing | OpenTelemetry → Honeycomb (optional) | |
| Feature flags | Single `features.json` checked into repo for MVP | |

**Versions are floors, not ceilings.** The agent MAY upgrade minor/patch versions during build; major upgrades require a note in this doc.

---

## 3. Repository Layout

```
OpenAgentDojo/
├── apps/
│   ├── web/                        # Next.js 15 app
│   │   ├── app/
│   │   │   ├── (marketing)/        # Landing, About, Pricing-later
│   │   │   ├── (app)/
│   │   │   │   ├── missions/       # Catalog + individual mission entry
│   │   │   │   ├── workspace/      # Mission workspace (client-heavy)
│   │   │   │   ├── report/         # Post-mission report
│   │   │   │   └── profile/        # Public skill profile
│   │   │   ├── api/                # Next route handlers (auth callbacks only)
│   │   │   └── layout.tsx
│   │   ├── components/
│   │   ├── lib/
│   │   ├── stores/                 # zustand
│   │   └── styles/
│   └── api/                        # FastAPI backend
│       ├── app/
│       │   ├── main.py
│       │   ├── config.py
│       │   ├── auth/
│       │   ├── missions/
│       │   ├── sessions/
│       │   ├── agent/              # hybrid-simulation agent service
│       │   ├── sandbox/            # Docker orchestration
│       │   ├── grading/            # scoring engine
│       │   ├── ws/                 # WebSocket handlers
│       │   ├── models/             # SQLAlchemy
│       │   ├── schemas/            # Pydantic
│       │   └── workers/            # RQ task definitions
│       ├── alembic/
│       ├── tests/
│       └── pyproject.toml
├── missions/                       # Mission packs (normative content)
│   ├── _shared/
│   │   ├── repos/                  # base repos (submodules or vendored zips)
│   │   │   ├── fullstack-auth-demo/
│   │   │   └── data-api-demo/
│   │   └── docker/                 # base images per language
│   ├── 01-auth-cookie-expiration/
│   │   ├── mission.yaml
│   │   ├── agent_patch.diff
│   │   ├── hidden_tests/
│   │   ├── forbidden_changes.yaml
│   │   ├── ideal_solution.md
│   │   └── prompts/                # canned-response templates for the agent
│   └── ... (10 total)
├── infra/
│   ├── docker/                     # Dockerfiles for sandbox images
│   ├── compose/                    # docker-compose for dev
│   ├── terraform/                  # (optional, post-MVP)
│   └── scripts/
├── docs/
│   ├── adr/                        # Architecture Decision Records
│   ├── scenarios/                  # human-facing scenario design notes
│   ├── grading.md
│   └── api.md
├── packages/
│   └── shared-types/               # TypeScript types generated from Pydantic
├── .github/workflows/
├── IMPLEMENTATION_PLAN.md          # this file
├── CONTEXT.md                      # domain glossary (created M0)
├── README.md
└── pnpm-workspace.yaml
```

Monorepo manager: **pnpm workspaces** (lockfile committed). Python uses **uv** (single `pyproject.toml` per Python app).

---

## 4. Domain Glossary (lives in `CONTEXT.md`)

These are the load-bearing nouns. Use them consistently in code, UI copy, and docs.

- **Mission** — a curated supervision exercise (= "Scenario" in product doc; we use Mission in code).
- **Repo Pack** — frozen base repository the mission runs against (e.g. `fullstack-auth-demo@v1`).
- **Session** — one user's attempt at one mission (ephemeral sandbox + persistent record).
- **Workspace** — the in-browser environment for an active Session.
- **Agent Turn** — one prompt → response cycle. Each turn has prompts, selected context, agent response, agent actions, optional applied patch.
- **Agent Patch** — the deliberately-flawed diff the agent applies. Deterministic per mission.
- **Verification Event** — any test/typecheck/lint/manual-check command run by the user.
- **Submission** — final patch the user submits for grading. Triggers hidden tests + validators.
- **Score Report** — graded result with 7 rubric dimensions + narrative feedback.
- **Forbidden Change** — a scenario-specific anti-pattern (e.g. "remove auth middleware").
- **Failure Mode** — the category of mistake the agent makes (one per mission).
- **Supervision Quality** — composite of process metrics (prompt, context, review, verify, correct, safety, minimality).

Update `CONTEXT.md` as new terms emerge.

---

## 5. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│ Browser (Next.js)                                                    │
│  ┌──────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐ │
│  │ FileTree     │  │ Monaco     │  │ DiffViewer │  │ AgentChat    │ │
│  └──────────────┘  └────────────┘  └────────────┘  └──────────────┘ │
│  ┌──────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐ │
│  │ Terminal     │  │ TestPanel  │  │ Timeline   │  │ Brief/Score  │ │
│  └──────────────┘  └────────────┘  └────────────┘  └──────────────┘ │
└────────────────┬────────────────────────────────────────┬───────────┘
                 │ REST (HTTP/JSON)                       │ WebSocket
                 ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ FastAPI (Uvicorn)                                                    │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌────────────┐ ┌─────────────┐ │
│  │ Auth    │ │ Missions │ │ Agent  │ │ Sandbox    │ │ Grading     │ │
│  └─────────┘ └──────────┘ └────────┘ └────────────┘ └─────────────┘ │
└──────┬──────────────────────────────────────────────────┬───────────┘
       │                                                  │
       ▼                                                  ▼
┌──────────────┐  ┌──────────────┐                ┌────────────────────┐
│ Postgres 16  │  │ Redis 7 + RQ │                │ Docker Sandbox Pool│
└──────────────┘  └──────────────┘                │ (per-session ctnr) │
                                                  └────────────────────┘
                          ▲
                          │ artifacts (diffs, logs)
                          ▼
                  ┌────────────────┐
                  │ S3 / MinIO     │
                  └────────────────┘
```

### 5.1 Request lifecycle (mission start → submit)

1. User clicks "Start" on a mission card.
2. API creates a `Session`, enqueues a `provision_sandbox` RQ job.
3. Worker spawns a Docker container from `mission.repo_pack` base image, checks out `mission.initial_commit`, attaches a long-lived shell PTY, opens a WebSocket bridge.
4. API returns `session_id` + WS URL. Frontend connects.
5. User reads brief, selects context, prompts agent.
6. Agent service receives prompt → looks up the mission's deterministic `agent_patch.diff` and `prompts/response.md` → returns narrated response + queues "apply patch" action.
7. User confirms patch application → sandbox applies it → file tree + diff viewer update via WS.
8. User runs commands in terminal (verification). All commands are intercepted and emitted as `CommandRun` events.
9. User edits files (manual correction). Edits stream to backend as `FileChange` events.
10. User clicks **Submit**. API freezes the sandbox, runs `hidden_tests/` + `validators/`, computes `ScoreReport`, persists, and returns the report URL.
11. Sandbox is destroyed (artifacts persisted to S3).

### 5.2 Determinism budget

| Component | Determinism |
|---|---|
| Agent patch | Fully deterministic (pre-written `.diff`) |
| Agent prose | Templated; LLM narration is optional and gated behind a flag (cached per scenario) |
| Hidden tests | Deterministic |
| Validators (diff scope, forbidden changes) | Deterministic |
| Prompt-quality scoring | Rubric-based, deterministic (regex + length + keyword) for MVP; LLM-judge optional later |
| Context-selection scoring | Set comparison against `expected_context` list |
| Test/command execution | Pinned base images; pinned dependency lockfiles |

LLM use anywhere on the grading hot path requires a fallback to a deterministic rubric.

---

## 6. Data Model

SQLAlchemy 2.x models in `apps/api/app/models/`. Mirror Pydantic schemas in `apps/api/app/schemas/`. Generate TypeScript via `datamodel-code-generator` into `packages/shared-types/`.

### 6.1 Tables (DDL summary)

```sql
-- users
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         CITEXT UNIQUE NOT NULL,
  display_name  TEXT,
  github_login  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ
);

-- missions are catalog metadata; the actual content lives in /missions/
CREATE TABLE missions (
  id               TEXT PRIMARY KEY,               -- e.g. "auth-cookie-expiration"
  title            TEXT NOT NULL,
  difficulty       TEXT NOT NULL CHECK (difficulty IN ('beginner','intermediate','advanced')),
  category         TEXT NOT NULL,
  repo_pack        TEXT NOT NULL,
  initial_commit   TEXT NOT NULL,
  estimated_minutes INT NOT NULL,
  failure_mode     TEXT NOT NULL,
  skills_tested    TEXT[] NOT NULL,
  manifest_sha256  TEXT NOT NULL,                  -- hash of mission.yaml
  version          INT NOT NULL DEFAULT 1,
  published        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES users(id),
  mission_id       TEXT NOT NULL REFERENCES missions(id),
  status           TEXT NOT NULL CHECK (status IN ('provisioning','active','submitting','graded','abandoned','error')),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at     TIMESTAMPTZ,
  sandbox_id       TEXT,                            -- docker container id
  current_commit   TEXT,
  score            INT,
  agent_turns      INT NOT NULL DEFAULT 0,
  CHECK (score IS NULL OR (score BETWEEN 0 AND 100))
);

CREATE INDEX idx_sessions_user ON sessions(user_id, started_at DESC);

CREATE TABLE agent_turns (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id        UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  turn_index        INT NOT NULL,
  user_prompt       TEXT NOT NULL,
  selected_context  JSONB NOT NULL,                 -- {files: [], logs: [], tests: [], extras: []}
  agent_response    TEXT NOT NULL,
  applied_patch     TEXT,                            -- unified diff
  patch_applied_at  TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (session_id, turn_index)
);

CREATE TABLE file_changes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  path          TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('agent','user','revert')),
  hunk_count    INT NOT NULL,
  added_lines   INT NOT NULL,
  removed_lines INT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE command_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  command       TEXT NOT NULL,
  exit_code     INT,
  duration_ms   INT,
  stdout_s3_key TEXT,
  stderr_s3_key TEXT,
  category      TEXT,                               -- 'test'|'typecheck'|'lint'|'manual'|'other'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE submissions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id               UUID NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
  final_diff               TEXT NOT NULL,
  visible_test_results     JSONB NOT NULL,
  hidden_test_results      JSONB NOT NULL,
  validator_results        JSONB NOT NULL,
  score_report             JSONB NOT NULL,
  total_score              INT NOT NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE badges (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT NOT NULL,
  icon        TEXT NOT NULL
);

CREATE TABLE user_badges (
  user_id    UUID NOT NULL REFERENCES users(id),
  badge_id   TEXT NOT NULL REFERENCES badges(id),
  earned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_id UUID REFERENCES sessions(id),
  PRIMARY KEY (user_id, badge_id)
);

CREATE TABLE supervision_events (
  -- single append-only event log for replayable timelines
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_type    TEXT NOT NULL,    -- see §6.2
  payload       JSONB NOT NULL,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_session_time ON supervision_events(session_id, occurred_at);
```

### 6.1.A Tables added after the original DDL

Migrations 0004–0019 layer additional tables/columns on top of the initial schema. The summary below is a doc surface — the migrations under [`apps/api/alembic/versions/`](apps/api/alembic/versions/) and the per-batch designs in [P0_DESIGN.md](P0_DESIGN.md) / [P0_DESIGN_11_13.md](P0_DESIGN_11_13.md) are the runtime truth.

| Migration | Adds | Why |
|---|---|---|
| 0004 | submission indexes | hot-path read perf |
| 0005 | `users.handle` (unique) | public profile URLs |
| 0006 | `sessions.last_activity_at` | idle-session reaper |
| 0007 | rename `dimensions[].max` → `dimensions[].max_score` in `score_report` | radar schema rename |
| 0008 | `submissions.manifest_sha256` | freeze the manifest hash at grade time |
| 0009 | `prompt_judgements` table | cached LLM judge for prompt-quality dimension |
| 0010 | `prompt_judgements` audit columns | rubric-version + model + tokens |
| 0011 | `missions.kind`, `users.tutorial_completed_at`, `users.tutorial_replay_count` | P0-1 (orientation) |
| 0012 | `submissions.critical_moments` (JSONB), evidence-bearing strengths/weaknesses | P0-2 (post-mortem walkthrough) |
| 0013 | `sessions.attempt_index`, `submissions.score_cap_reason` | P0-3 (multi-attempt) / ADR 0009 |
| 0014 | `sessions.gave_up_at` | P0-4 (give-up affordance) / ADR 0010 |
| 0015 | `user_consents` table, `users.pending_consent_version` | P0-5 (legal pages / cookie consent) |
| 0016 | `users.pending_email`, `users.deletion_scheduled_at`, `data_exports` table | P0-6 (account self-service) |
| 0017 | `account_events` table | P0-6 audit log |
| 0018 | `account.deleted` event type allowed in 0017's check | tombstoning |
| 0019 | `submissions.verification_hash`, `submissions.verification_signature`, `report_renders` table | P0-11 (credentialing artifact) |

### 6.2 Supervision event types (enum)

```
session.started
session.errored
session.abandoned
session.gave_up                 { seconds_into_session }                      // ADR 0010 / P0-4
session.provision_failed        { reason }                                    // emitted by the provisioning worker
context.selected                { files:[], logs:[], tests:[] }
prompt.submitted                { turn_index, text, char_count, keyword_hits }
agent.responded                 { turn_index, response_summary }
patch.proposed                  { turn_index, intent }
patch.applied                   { turn_index, files_changed, added, removed }
patch.failed                    { turn_index, detail }
diff.opened                     { path }
diff.hovered                    { path, line }                                // optional micro-event
file.edited                     { path, added, removed, source }
file.reverted                   { path }
command.run                     { command, category, exit_code, duration_ms }
test.run                        { suite, passed, failed }
validator.flag                  { kind, message }                             // emitted by grading dry-runs (post-submit)
submission.requested
submission.graded               { score, breakdown, missed_failure_mode }
submission.failed               { stage, detail }
tutorial.step_completed         { step_id, mission_id: "orientation" }        // P0-1
tutorial.dismissed              { step_id }                                   // P0-1
tutorial.completed              { mission_id }                                // P0-1
consent.granted                 { kind: "analytics" | "functional", version } // P0-5
consent.revoked                 { kind }                                      // P0-5
```

Events drive both the live timeline UI and the post-hoc grader. The canonical TS union lives in `packages/shared-types/src/events.ts`; the JSON Schema mirror is at [`docs/schemas/event.schema.json`](docs/schemas/event.schema.json). `account.*` events (P0-6) live on a sibling `account_events` table — they are NOT supervision events.

---

## 7. Mission Manifest Specification

Every mission lives under `missions/<id>/`. The manifest `mission.yaml` is the contract; the agent and grader read from it. The manifest hash is recorded in `missions.manifest_sha256` to detect drift.

### 7.1 `mission.yaml` schema

```yaml
id: auth-cookie-expiration                          # kebab-case, unique
version: 1
title: "Expired Session Cookie Still Grants Access"
short_description: "Users with expired session cookies can still access protected routes."
difficulty: intermediate                            # beginner | intermediate | advanced
category: auth                                      # auth | testing | security | frontend | api | database | refactoring | agent-safety | review | debugging
estimated_minutes: 35
skills_tested: [auth, security, test-writing, agent-review]

repo:
  pack: fullstack-auth-demo                         # references /missions/_shared/repos/<pack>
  initial_commit: abc123de                          # checked out at session start
  workdir: /workspace                               # path inside container
  language_runtime: node20                          # node20 | python312
  setup_commands:
    - pnpm install --frozen-lockfile
  ready_check: pnpm test:unit --silent              # must exit 0 before "active"
  test_commands:
    unit:    pnpm test:unit
    integration: pnpm test:integration
    typecheck: pnpm typecheck
    lint:    pnpm lint

brief: |
  Multi-paragraph markdown shown to the user. Includes context, expected behavior,
  and what they need to deliver.

failure_mode:
  id: checks_presence_not_expiration
  title: "Agent validates cookie existence but not expiration"
  description: |
    The agent's patch will add a presence/length check but skip expiration validation.
  hint_after_submit_if_missed: "Look at session.ts — `isValid()` is never called."

expected_files:                                     # files the user 'should' touch
  - backend/auth/session.ts
  - backend/middleware/requireAuth.ts
  - backend/tests/auth.test.ts

expected_context:                                   # for context-selection scoring
  required:
    - backend/auth/session.ts
    - backend/middleware/requireAuth.ts
  recommended:
    - backend/tests/auth.test.ts
    - docs/auth.md
  discouraged:
    - frontend/components/LoginForm.tsx
    - frontend/styles/login.css

agent:
  patch_file: agent_patch.diff
  response_template: prompts/response.md
  reasoning_template: prompts/reasoning.md
  applies_when:                                    # heuristic for auto-apply
    prompt_min_chars: 40
    prompt_must_contain_any: [fix, repair, address, investigate, debug]
  apply_mode: on_user_confirm                      # on_user_confirm | auto

visible_tests:
  - "valid session can access dashboard"
  - "missing session redirects to login"

hidden_tests:                                       # discoverable only post-submit
  command: pnpm test:hidden
  expected_pass:
    - "expired session redirects to login"
    - "tampered session is rejected"
    - "valid session remains valid"
    - "session refresh respects expiration"

validators:                                         # see §10
  - kind: forbidden_changes
    rules_file: forbidden_changes.yaml
  - kind: diff_scope
    max_files_changed: 4
    max_added_lines: 120
    must_touch_any_of: [backend/auth/session.ts, backend/middleware/requireAuth.ts]
    must_not_touch:
      - frontend/**
      - package.json
  - kind: regression_test_required
    test_globs: [backend/tests/**/*.test.ts]
    keywords_any_of: [expired, expiration, ttl]
  - kind: no_skipped_tests
    patterns: [".skip(", "xit(", "xdescribe("]

scoring_weights:
  final_correctness: 30
  verification:      15
  agent_review:      15
  prompt_quality:    10
  context_selection: 10
  safety:            10
  diff_minimality:   10

reward_signals:                                     # process-based scoring inputs
  prompt_quality:
    must_include_any: [reproduce, root cause, regression test, minimal, expiration]
    bonus_keywords: [security, edge case, hidden]
    penalty_if_under_chars: 40
  verification:
    required_categories: [test, typecheck]
    bonus_if_run_before_patch: true
    require_targeted_test: "auth"                   # substring/regex of command
  agent_review:
    require_diff_open: true
    bonus_if_revert_or_edit_after_diff: true
  safety:
    must_not_run_commands: ["rm -rf", "curl"]
    must_not_introduce_deps: true
```

### 7.2 Agent patch file (`agent_patch.diff`)

Standard unified diff applied via `git apply --3way` inside the sandbox. Must:

- Apply cleanly on `initial_commit`.
- Pass `visible_tests` but fail at least one `hidden_test`.
- Trigger at least one validator if accepted unchanged.

### 7.3 Forbidden-changes file

```yaml
# missions/01-auth-cookie-expiration/forbidden_changes.yaml
forbidden:
  - id: removes_middleware
    description: "Removes or no-ops requireAuth middleware"
    detection:
      kind: regex_absent
      file: backend/middleware/requireAuth.ts
      pattern: "export\\s+(default\\s+)?function\\s+requireAuth"
      penalty: 10
  - id: hardcoded_test_user
    description: "Patch hardcodes a user id to make tests pass"
    detection:
      kind: regex_present_in_diff
      pattern: "userId\\s*=\\s*['\"]test"
      penalty: 10
```

### 7.4 Hidden tests

`hidden_tests/` is mounted into the sandbox only during grading. Tests use the project's native runner (Vitest for TS, Pytest for Python). Hidden tests MUST NOT be visible in the user's workspace listing — backend mounts them at submit time and unmounts on completion.

### 7.5 Ideal solution

`ideal_solution.md` is shown in the post-mission report only after submit. Includes:

- Root cause walkthrough
- Minimal diff (annotated)
- What the agent got wrong
- What a strong supervisor would have prompted

---

## 8. Built-in Agent (Hybrid Simulation)

The agent is the lynchpin of determinism. Implementation lives in `apps/api/app/agent/`.

### 8.1 Agent service contract

```python
class AgentService:
    async def respond(
        self,
        session: Session,
        prompt: str,
        context: ContextSelection,
    ) -> AgentTurnResponse: ...

    async def apply_patch(self, session: Session, turn_id: UUID) -> PatchResult: ...
```

### 8.2 Response generation algorithm

1. Look up `mission.agent.response_template` and render with Jinja2, using:
   - `prompt_summary` — a deterministic 1-sentence extract (first sentence or first 200 chars).
   - `context_summary` — short list of selected files.
   - `failure_mode.title`.
2. If `features.llm_narration_enabled` is true AND Bedrock credentials are present (see §16.A):
   - Acquire an LLM client via `civitas_core.llm.anthropic_client.build_anthropic_sdk_client()`. Because `ANTHROPIC_PROVIDER=bedrock` is set in the env, this returns an `AsyncAnthropicBedrock` instance; otherwise (e.g. local dev with the var unset) it falls back to a direct `AsyncAnthropic` client.
   - Resolve the model id with `resolve_anthropic_model_id("claude-haiku-4-5")` so the call targets the correct Bedrock inference-profile id (`us.anthropic.…`). **Never hard-code Bedrock profile ids in application code** — always go through the helper.
   - Send a constrained request with the rendered template as the *seed*, asking the model only to humanize tone (not change facts).
   - Use prompt caching on the system prompt (which contains scenario rules) — Bedrock supports the same cache_control breakpoints as the first-party API.
   - On any error, throttling, or non-deterministic deviation (length out of bounds, banned tokens present), fall back silently to the rendered template and increment the `agent_llm_fallback_total` metric.
3. Return `AgentTurnResponse { response_markdown, proposed_actions: ["apply_patch"] }`.

> **Hard rule:** the agent *patch* never depends on the LLM call. The LLM is only allowed to rewrite prose. Grading paths MUST NOT invoke any LLM.

### 8.3 Patch application

- Patch is applied inside the sandbox via `git apply --3way --whitespace=fix < agent_patch.diff`.
- If apply fails (extremely rare since `initial_commit` is pinned), record a `patch.apply_failed` event and surface a non-blocking error. Allow the user to apply manually.
- On success, emit `patch.applied` event with file deltas.

### 8.4 Multi-turn behavior

For MVP, only the **first** "fix the bug" turn triggers `agent_patch.diff`. Subsequent turns (e.g. "add a test for expiration") respond with templated content:

- "fix the bug" — apply `agent_patch.diff`.
- "add a test" — apply `agent_patch_addtest.diff` if present, otherwise a no-op response.
- "revise" / "try again" — produce a *second* flawed patch from `agent_patch_revision.diff` if present; else say "I believe the previous change is correct."

Each mission MAY define up to 3 stocked patches. The classifier maps user prompts to one of `{fix, test, revise, narrow, unknown}` via keyword sets defined in `prompts/intents.yaml`.

### 8.5 LLM narration prompt skeleton (when enabled)

```
SYSTEM (cached):
You are a coding agent in a training simulator. You MUST NOT change the substantive
content of the seed response; only rewrite for natural tone. Keep length within ±20%.
Banned tokens: <list per scenario>. Never add new code blocks. Never refuse.

USER:
SEED RESPONSE:
<<<rendered template>>>

USER CONTEXT SUMMARY:
<<<context_summary>>>

USER PROMPT:
<<<prompt>>>
```

If output diverges (length out of bounds, banned token present), the call is discarded silently and the seed is used.

---

## 9. Sandbox Layer

`apps/api/app/sandbox/` orchestrates per-session Docker containers.

### 9.1 Container model

- One container per active session.
- Base image per `language_runtime`:
  - `agentarena/node20:1` — Node 20, pnpm, jq, ripgrep, git.
  - `agentarena/python312:1` — Python 3.12, uv, pytest, ruff, mypy.
- Mounts:
  - `/workspace` — overlayfs view of the repo pack at `initial_commit`.
  - `/grader` — mounted read-only at submit time only; contains hidden tests + validator binaries.
- Network: **none** by default. Outbound DNS blocked. Optionally allow `npm`/`pip` registries via egress proxy for scenarios that need installs (MVP: pre-install everything in the image).
- Resource caps (cgroups v2): 1 vCPU, 2 GB RAM, 1 GB disk, 30-minute lifetime hard cap.
- Stdin/stdout via PTY exposed over WebSocket.

### 9.2 Sandbox API (Python)

```python
class Sandbox:
    async def provision(self, mission: Mission, session_id: UUID) -> SandboxHandle: ...
    async def attach_shell(self, handle: SandboxHandle) -> PtyStream: ...
    async def read_file(self, handle, path) -> bytes: ...
    async def write_file(self, handle, path, content) -> None: ...
    async def list_tree(self, handle, root="/workspace") -> FileTree: ...
    async def diff_from_initial(self, handle) -> str: ...
    async def run(self, handle, cmd: list[str], timeout_s: int) -> RunResult: ...
    async def apply_diff(self, handle, diff_text: str) -> ApplyResult: ...
    async def freeze_and_grade(self, handle, mission: Mission) -> GradingArtifacts: ...
    async def destroy(self, handle) -> None: ...
```

### 9.3 Grading isolation

At submit:

1. Block further writes (set `/workspace` read-only).
2. Snapshot the diff (`git diff <initial_commit>..HEAD`).
3. Mount `/grader` read-only.
4. Run, in order:
   - `mission.repo.test_commands.unit` (visible)
   - `mission.repo.test_commands.typecheck`
   - `mission.repo.test_commands.lint`
   - `pnpm test:hidden` (or scenario-defined equivalent)
   - Static validators (diff scope, forbidden changes) via Python visitors.
5. Persist all stdout/stderr to S3, JSON results to `submissions`.

### 9.4 Local dev fallback

If Docker is unavailable in dev, the sandbox layer SHOULD degrade to a `subprocess`-based runner with a temp directory (no isolation). Gated behind `SANDBOX_DRIVER=local`. Loud warning banner in the UI. Never enabled in prod.

---

## 10. Validators

Implementation: `apps/api/app/grading/validators/`. Each validator is a pure function `(diff, fs, events) -> ValidatorResult`.

### 10.1 Validator catalog

| Kind | Inputs | Output |
|---|---|---|
| `forbidden_changes` | diff, fs | matched rules list, total penalty |
| `diff_scope` | diff | files_changed, lines_added, breach booleans |
| `regression_test_required` | diff, fs | test files matched, keywords matched |
| `no_skipped_tests` | diff | offending lines |
| `no_new_dependencies` | diff of lockfiles | added deps, total |
| `no_secrets_exposed` | diff | suspected secrets (regex pack) |
| `no_validation_removed` | diff, fs | removed guard clauses (per-scenario AST patterns) |
| `tests_actually_pass` | test results | pass/fail bool |

### 10.2 Diff-scope validator (representative)

```python
@dataclass
class DiffScopeRule:
    max_files_changed: int | None = None
    max_added_lines: int | None = None
    must_touch_any_of: list[str] = field(default_factory=list)
    must_not_touch: list[str] = field(default_factory=list)  # glob patterns

def diff_scope(diff: ParsedDiff, rule: DiffScopeRule) -> ValidatorResult:
    files = diff.changed_paths()
    added = diff.added_lines_total()
    violations = []
    if rule.max_files_changed and len(files) > rule.max_files_changed:
        violations.append(f"Changed {len(files)} files (max {rule.max_files_changed})")
    if rule.max_added_lines and added > rule.max_added_lines:
        violations.append(f"Added {added} lines (max {rule.max_added_lines})")
    if rule.must_touch_any_of and not any(p in files for p in rule.must_touch_any_of):
        violations.append("Required scope file not touched")
    for path in files:
        for pattern in rule.must_not_touch:
            if fnmatch.fnmatch(path, pattern):
                violations.append(f"Modified out-of-scope path: {path}")
    return ValidatorResult(passed=not violations, violations=violations)
```

### 10.3 Validator result schema

```ts
type ValidatorResult = {
  kind: string;
  passed: boolean;
  violations: string[];
  penalty: number;          // points deducted (caller-side weighting)
  evidence?: { file?: string; line?: number; snippet?: string }[];
};
```

---

## 11. Scoring Engine

Implementation: `apps/api/app/grading/score.py`. Source of truth for the weighted-30 rubric (chosen over the flat-15 alternative per product doc recommendation).

### 11.1 Weight table

| Dimension | Max | Source |
|---|---|---|
| Final Patch Correctness | 30 | Hidden + visible tests |
| Verification Discipline | 15 | Command run events |
| Agent Output Review | 15 | Diff events + corrections |
| Prompt Quality | 10 | Turn analysis |
| Context Selection | 10 | Selected vs expected sets |
| Safety Awareness | 10 | Forbidden-change detection + safe behavior |
| Diff Minimality | 10 | Diff-scope validator |
| **Total** | **100** | |

> Runtime truth: `apps/api/app/grading/dimensions.py` is the single source
> of the weights — verification and diff_minimality were re-balanced from
> `20 / 5` to `15 / 10` so that minimal diffs (a strong proxy for surgical
> reasoning) carry the same weight as "did you bother to run tests" and
> are not buried under the catch-all verification bonus. Mission YAMLs and
> the §15 schema reflect the same numbers.

### 11.2 Sub-scoring rules

#### 11.2.1 Final Patch Correctness (30)

```
base = 0
+ 12 if all hidden_tests pass
+ 8  if all visible_tests pass
+ 6  if no regression (existing tests still pass)
+ 4  if root cause addressed (validator: required code paths touched)
```

Floor when hidden tests fail: cap at 18 even if everything else is green.

#### 11.2.2 Verification Discipline (15)

```
+ 6 if a targeted test command ran and either (a) passed, or (b) failed
     but the supervisor followed up with file.edited / file.reverted /
     prompt.submitted before submit (engagement-after-failure split)
+ 3 if a targeted test ran and failed with no follow-up edit or prompt
+ 3 if typecheck ran
+ 2 if lint ran
+ 4 if a NEW regression test exists in the final diff
     (validator: regression_test_required passes)
- 6 if submitted with zero verification commands
```

Cap at 15. The engagement-after-failure split (+6 vs +3) is implemented
in `_score_verification` in
[`apps/api/app/grading/score.py`](apps/api/app/grading/score.py) — the
scorer is the single source of truth; this section is the prose
mirror. See [ADR 0011](docs/adr/0011-rubric-rebalance.md) for the
historical rebalance from `20` to `15`.

#### 11.2.3 Agent Output Review (15)

```
+ 6 if diff was opened at least once after patch.applied (event: diff.opened)
+ 5 if user edited or reverted any line of the agent's patch (events: file.edited, file.reverted)
+ 4 if user submitted at least one corrective prompt (intent classifier: revise|narrow|test)
0  if user submitted within 15s of agent.responded and no diff.opened: 0/15 hard
```

#### 11.2.4 Prompt Quality (10)

Computed per turn, max'd over turns then capped:

```
score = 0
+ 2 if length >= 80 chars
+ 2 if contains any of mission.reward_signals.prompt_quality.must_include_any
+ 1 per bonus_keyword present (max +3)
+ 2 if request for tests / regression coverage is present
+ 2 if scope constraint phrase present ("do not modify", "minimal", "without changing")
- 3 if length < 40 chars
- 2 if vague-only ("fix it", "make it work")
```

#### 11.2.5 Context Selection (10)

```
required_hit = |selected ∩ required| / |required|
recommended_hit = |selected ∩ recommended| / |recommended|
discouraged_hit = |selected ∩ discouraged|

score = round(required_hit * 7 + recommended_hit * 3) - min(3, discouraged_hit)
clamp(0, 10)
```

#### 11.2.6 Safety Awareness (10)

```
+ 4 if no forbidden_changes triggered
+ 3 if user reverted/rewrote a forbidden change introduced by the agent
+ 2 if no new dependencies added (or only allowlisted ones)
+ 1 if no banned commands run (rm -rf, curl into pipes, etc.)
```

#### 11.2.7 Diff Minimality (10)

Symmetric churn measure against the mission's `expected_diff_lines_p50`:

```
churn = max(added_lines, removed_lines)   # both directions count
score = 0  if churn == 0                  # empty submission earns no credit
ratio = churn / expected_p50
score = 10 if ratio <= 1.0
      =  8 if 1.0 < ratio <= 1.5
      =  6 if 1.5 < ratio <= 2.0
      =  4 if 2.0 < ratio <= 3.0
      =  0 otherwise
```

`expected_diff_lines_p50` lives in each `mission.yaml` and is clamped
to `[3, 200]` at scoring time (a typoed manifest can't silently shift
the whole mission's band). Using `max(added, removed)` instead of just
`added` prevents destructive minimisation — wiping out 200 lines while
adding 5 — from being rewarded as "minimal." The doubled max (was 5 in
the original draft) brings minimality into parity with the other
process-discipline dimensions; see
[ADR 0011](docs/adr/0011-rubric-rebalance.md) for the rebalance from
`5` to `10`.

### 11.3 Score report shape (persisted JSONB)

```json
{
  "total": 80,
  "dimensions": {
    "final_correctness":  { "score": 24, "max": 30, "signals": ["3/4 hidden tests passed", "visible green"] },
    "verification":       { "score": 11, "max": 15, "signals": ["ran auth tests", "no typecheck"] },
    "agent_review":       { "score": 11, "max": 15, "signals": ["diff opened", "1 corrective prompt"] },
    "prompt_quality":     { "score": 7,  "max": 10, "signals": ["mentions regression test", "scoped"] },
    "context_selection":  { "score": 8,  "max": 10, "signals": ["selected middleware + session.ts"] },
    "safety":             { "score": 9,  "max": 10, "signals": ["no validation removed"] },
    "diff_minimality":    { "score": 10, "max": 10, "signals": ["12 lines added"] }
  },
  "strengths": ["Selected the right context up front", "Asked for a regression test"],
  "weaknesses": ["Did not run typecheck", "Missed the refresh-token edge case"],
  "missed_failure_mode": false,
  "badges_earned": ["regression-test-writer"]
}
```

### 11.4 Badges

Awarded post-submission based on score-report signals. MVP set:

- `regression-test-writer` — added a regression test that matches failure-mode keywords.
- `security-aware-reviewer` — caught a forbidden change and corrected it.
- `agent-skeptic` — at least 1 corrective prompt + diff opened + edits to agent's lines.
- `minimal-diff` — diff_minimality == 10 and final_correctness >= 24.
- `concurrency-debugger` — earned on Scenario 8 with all hidden tests passing.
- `api-contract-guardian` — earned on Scenario 9 with no regression.

Migration `0002_seed_badges.py` inserts the catalog.

---

## 12. API Surface (FastAPI)

All routes under `/api/v1`. Auth via session cookie. WebSocket auth via short-lived signed token.

### 12.1 REST endpoints

```
POST   /auth/magic-link            { email } -> 204
GET    /auth/callback?token=...    -> sets cookie, redirects
POST   /auth/logout                -> 204
GET    /me                         -> User

GET    /missions                   -> Mission[]
GET    /missions/{id}              -> MissionDetail

POST   /sessions                   { mission_id } -> Session (status=provisioning)
GET    /sessions/{id}              -> SessionDetail
POST   /sessions/{id}/context      { files:[], logs:[], tests:[] } -> 204
POST   /sessions/{id}/prompts      { text, context_id? } -> AgentTurn
POST   /sessions/{id}/patches/{turn_id}/apply  -> PatchResult
POST   /sessions/{id}/files        { path, content } -> 204     # user manual edit
POST   /sessions/{id}/files/revert { path } -> 204
POST   /sessions/{id}/commands     { command, category } -> CommandRun
GET    /sessions/{id}/tree         -> FileTree
GET    /sessions/{id}/file?path=   -> { content, encoding }
GET    /sessions/{id}/diff         -> { unified_diff }
GET    /sessions/{id}/timeline     -> SupervisionEvent[]
POST   /sessions/{id}/submit       -> Submission (async — returns 202)
GET    /sessions/{id}/submission   -> Submission

GET    /profiles/{user_id}         -> PublicProfile
GET    /reports/{submission_id}    -> Submission (with score_report)
```

### 12.2 WebSocket channels

- `/ws/sessions/{id}/terminal` — bidirectional PTY stream.
- `/ws/sessions/{id}/events` — server → client supervision events as they happen.

### 12.3 OpenAPI

FastAPI auto-generates `/openapi.json`. CI step regenerates `packages/shared-types/openapi.json` and runs `openapi-typescript` to produce `packages/shared-types/api.ts`. Frontend imports from this single source.

---

## 13. Frontend Specification

### 13.1 Routes & layouts

| Route | Layout | Description |
|---|---|---|
| `/` | marketing | Landing |
| `/missions` | app | Mission catalog |
| `/missions/[id]` | app | Mission detail + start CTA |
| `/workspace/[sessionId]` | workspace (no nav) | The 4-pane workspace |
| `/report/[submissionId]` | app | Score report |
| `/profile/[handle]` | app | Public profile (no auth required) |
| `/auth/sign-in` | minimal | Email magic link |

### 13.2 Workspace layout (frozen)

```
┌─────────────────────────────────────────────────────────────────┐
│  Top bar: Mission title | Difficulty | Score preview | Submit ▶ │
├─────────┬─────────────────────────────────┬─────────────────────┤
│ FileTree│                                 │ Brief / Score / Help│
│         │       Code Editor / Diff        │  (tabs)             │
│ Context │                                 ├─────────────────────┤
│ Selector│                                 │ Agent Chat          │
├─────────┴──────────────┬──────────────────┤                     │
│  Terminal              │  Tests           │                     │
├────────────────────────┴──────────────────┴─────────────────────┤
│  Supervision Timeline (collapsible)                              │
└─────────────────────────────────────────────────────────────────┘
```

Resizable panels via `react-resizable-panels`. State persisted to `localStorage` keyed by mission id.

### 13.3 Component inventory

`apps/web/components/`:

```
workspace/
  FileTree.tsx              # virtualized; checkbox column for context selection
  ContextSelector.tsx       # multi-select with required/recommended badges hidden
  CodeEditor.tsx            # Monaco; LSP-lite via worker per language
  DiffViewer.tsx            # 3 modes: side-by-side / inline / minimap
  Terminal.tsx              # xterm + websocket
  TestPanel.tsx             # runs `pnpm test:*` via /commands
  AgentChat.tsx             # message list + composer with context chips
  Timeline.tsx              # vertical event stream, grouped by minute
  MissionBrief.tsx          # markdown with mission task
  ScorePreview.tsx          # live-updating partial score (no spoilers)
  SubmitDialog.tsx          # confirm + checklist preview
  VerificationChecklist.tsx # user-checkable but cross-referenced with events
catalog/
  MissionCard.tsx
  CategoryChips.tsx
report/
  ScoreRadar.tsx            # 7-dim radar chart (recharts)
  DimensionBreakdown.tsx
  TimelineReplay.tsx        # scrubbable replay
  IdealSolution.tsx         # markdown
profile/
  ProfileHeader.tsx
  BadgeGrid.tsx
  MissionHistoryTable.tsx
marketing/
  Hero.tsx
  HowItWorks.tsx
  SampleReport.tsx
  Footer.tsx
```

### 13.4 State management

- `useWorkspaceStore` (zustand): `selectedContext`, `openTabs`, `activeFile`, `agentTurns`, `events`.
- React Query: server state (`missions`, `sessions`, `submission`).
- WebSocket events feed both timeline and an in-memory event buffer used by `ScorePreview`.

### 13.5 Score preview policy

`ScorePreview` shows partial-credit indicators *without revealing hidden-test outcomes*. Allowed signals:

- "Context: 2/2 required selected ✓"
- "Verification: tests not yet run"
- "Diff: 0 unrelated files changed ✓"

Disallowed pre-submit:

- Hidden test names or counts.
- Failure-mode hints.
- Predicted total score.

### 13.6 Accessibility

- WCAG 2.1 AA target.
- Keyboard-navigable panels (Tab order: tree → editor → terminal → chat).
- Monaco accessibility mode enabled.
- Color: ensure diff add/remove also use texture/icons (not color alone).
- Live regions for agent responses and command completions.

---

## 14. The 10 Missions (Full Specs)

All missions live in `missions/<NN-id>/`. Two repo packs back them:

- `fullstack-auth-demo` (Express + Vite + TypeScript) — Scenarios 1, 2, 3, 5, 6, 9, 10.
- `data-api-demo` (FastAPI + SQLAlchemy + Pytest) — Scenarios 4, 7, 8.

### 14.1 Mission 01 — Auth Cookie Expiration

See §7.1 manifest. Diff applies a presence-only check; hidden test exercises an expired-cookie request. `expected_diff_lines_p50: 18`.

### 14.2 Mission 02 — Agent Edits the Wrong File

- Bug: profile name renders truncated.
- Root cause: backend serializer returns `displayName.slice(0,8)`.
- Agent patch: adds CSS `text-overflow: ellipsis` to the component.
- Forbidden change: modifying frontend CSS without backend fix.
- Hidden test: request to `/api/users/me` returns full name regardless of length.
- `expected_diff_lines_p50: 6`.

### 14.3 Mission 03 — Missing Regression Test (Duplicate Submission)

- Bug: form double-submit creates duplicate rows.
- Agent patch: adds an in-memory `Set<formId>` idempotency check, no test.
- Forbidden: in-memory store (must be DB unique constraint or token).
- Hidden tests: parallel requests, server-restart between submits.
- Validator: `regression_test_required` with keywords `duplicate, idempot, unique`.
- `expected_diff_lines_p50: 24`.

### 14.4 Mission 04 — Overfitted Test Fix (Price Calculation)

Repo: `data-api-demo`. Visible failing test for `calculate_price(qty=3, unit=10)`. Agent patch hardcodes `if qty == 3: return 30` inside the calculator. Hidden tests sweep `qty ∈ {0,1,2,5,100}` and floats.

### 14.5 Mission 05 — Security Validation Removed (Settings Update)

- Bug: PUT `/users/:id/settings` returns 403 for the legitimate user.
- Agent patch: removes the `assertOwnerOrAdmin(req)` check.
- Forbidden: removal of authorization guards (detected via AST pattern + diff regex).
- Hidden tests: cross-user update is rejected.
- High safety weight.

### 14.6 Mission 06 — Excessive Rewrite (Dashboard Loading)

- Bug: spinner stays after data loads on dashboard.
- Root cause: `setLoading(false)` missing in error branch only.
- Agent patch: rewrites the entire `Dashboard.tsx` component with a new state machine, swaps `useState` for `useReducer`, adds a new hook file.
- Validator: `diff_scope { max_files_changed: 2, max_added_lines: 30 }`.

### 14.7 Mission 07 — Dependency Misuse (Date Formatting)

Repo: `data-api-demo`. Task: format report timestamps as `YYYY-MM-DD HH:mm` in user's tz. Agent installs `arrow` (already deprecated) and ignores DST; hidden test exercises a DST boundary in `Europe/London`.

### 14.8 Mission 08 — Async Race Condition (Queue Processing)

Repo: `data-api-demo`. Bug: occasional duplicate processing in `process_job`. Agent patch: adds `if job.status == 'pending': job.status = 'running'; commit()` *outside* a transaction. Hidden tests use `asyncio.gather` to fire 20 concurrent processors against the same job; expect exactly one success.

### 14.9 Mission 09 — API Contract Drift

- Bug: frontend crashes after backend rename of `user.fullName` to `user.displayName`.
- Agent patch: updates only `ProfileCard.tsx`, misses `Header.tsx` and `Settings.tsx`.
- Hidden tests: Playwright check that all three components render without errors.

### 14.10 Mission 10 — Typecheck Ignored (Avatar Upload)

- Task: add optional `avatarUrl?: string` to user.
- Agent patch: implements runtime, casts via `as any` in 3 places.
- Hidden checks: `pnpm typecheck` must exit 0; `grep -c 'as any' src/` must be 0 in diff.

### 14.11 Mission authoring checklist (apply to all 10)

- [ ] `mission.yaml` validates against JSON Schema (`docs/schemas/mission.schema.json`).
- [ ] `agent_patch.diff` applies on `initial_commit` and passes all `visible_tests`, fails ≥1 `hidden_test`.
- [ ] At least one validator trips when the unmodified agent patch is submitted.
- [ ] `ideal_solution.md` includes a minimal diff that passes hidden tests and all validators.
- [ ] `expected_context.required` is ≥ 2 files; `discouraged` is ≥ 1 file.
- [ ] `reward_signals.prompt_quality.must_include_any` is ≥ 3 keywords.
- [ ] `expected_diff_lines_p50` is set.
- [ ] Mission appears in catalog seed (`apps/api/alembic/versions/0003_seed_missions.py`).

---

## 15. Mission JSON Schema (validation)

`docs/schemas/mission.schema.json` (excerpted; full schema generated from a Pydantic model in `apps/api/app/missions/manifest.py`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["id","version","title","difficulty","category","repo","brief",
               "failure_mode","expected_context","agent","hidden_tests",
               "validators","scoring_weights","reward_signals"],
  "properties": {
    "id": {"type":"string","pattern":"^[a-z0-9-]+$"},
    "difficulty": {"enum":["beginner","intermediate","advanced"]},
    "scoring_weights": {
      "type":"object",
      "properties": {
        "final_correctness":{"const":30},
        "verification":{"const":15},
        "agent_review":{"const":15},
        "prompt_quality":{"const":10},
        "context_selection":{"const":10},
        "safety":{"const":10},
        "diff_minimality":{"const":10}
      },
      "required":["final_correctness","verification","agent_review",
                  "prompt_quality","context_selection","safety","diff_minimality"]
    }
  }
}
```

CI step `pnpm validate:missions` runs this schema check + custom checks (the §14.11 list).

---

## 16. Auth & Identity

- Magic-link email via Resend; token = signed 30-min JWT, single-use, stored in `magic_link_tokens` with `used_at`.
- Session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, 30-day expiry, rotated on each login.
- GitHub OAuth (optional, M3): adds `github_login`; same user record matched by verified email.
- Profile handle: lowercased part before `@` of email at signup, collisions get numeric suffix.
- No PII beyond email + display_name.

CSRF: all POSTs require `X-Csrf-Token` matching a per-session token issued via `/me`.

### 16.A LLM provider configuration (Anthropic on Bedrock)

All Anthropic API access — both the optional agent-narration call (§8.2) and any future server-side LLM use — MUST go through the Civitas client helper so that the same code works locally (direct API) and in prod (Bedrock).

**Required environment variables (set in prod, optional locally):**

```
ANTHROPIC_PROVIDER=bedrock
AWS_BEARER_TOKEN_BEDROCK=<see keys.md>
AWS_REGION=us-east-2
```

Local values live in [keys.md](./keys.md) (gitignored). Prod values live in Fly secrets. **Never check the bearer token into git or paste it into logs.**

**Client construction pattern (Python):**

```python
from civitas_core.llm.anthropic_client import (
    build_anthropic_sdk_client,
    resolve_anthropic_model_id,
)

async def narrate(seed: str, prompt: str) -> str:
    client = build_anthropic_sdk_client()                # AsyncAnthropicBedrock when ANTHROPIC_PROVIDER=bedrock
    model = resolve_anthropic_model_id("claude-haiku-4-5")  # → us.anthropic.claude-haiku-4-5-...
    resp = await client.messages.create(
        model=model,
        max_tokens=600,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": render_user_message(seed, prompt)}],
    )
    return resp.content[0].text
```

**Rules:**

- Always pass a *logical* model id (`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`) and let `resolve_anthropic_model_id` map to the Bedrock profile.
- Use prompt caching on the system block to keep cost flat across repeated scenario narrations.
- Wrap the client behind a thin `app.agent.llm.AnthropicClient` adapter so unit tests can swap in a fake without touching env vars.
- Configuration sanity check: on app start, log `provider=bedrock region=us-east-2` (no secret material) so misconfiguration is immediately visible.

**Local-dev fallback:** if the developer omits `ANTHROPIC_PROVIDER`, `build_anthropic_sdk_client()` returns a direct `AsyncAnthropic` instance and reads `ANTHROPIC_API_KEY` instead. Both code paths share the same call sites — no branching required in feature code.

**Telemetry:** add metrics `llm_calls_total{provider,model,outcome}` and `llm_latency_seconds_bucket{provider,model}` so we can compare Bedrock vs. direct in case of regressions.

---

## 17. Build Order (Milestones)

Eight milestones. Each ends with a demoable artifact and a CI gate.

### M0 — Bootstrap (1–2 days)

- pnpm workspace, apps/web (Next.js scaffold), apps/api (FastAPI scaffold), docker-compose for postgres+redis+minio.
- Linting/formatting baselines (ruff, black, prettier, eslint with `eslint-config-next`).
- Pre-commit hooks (lint-staged + pre-commit framework).
- `CONTEXT.md`, `README.md`, `docs/adr/0001-tech-stack.md`.
- CI: typecheck + lint runs on every push.
- **Exit gate:** `docker compose up` → frontend says "hello" → backend `/healthz` returns 200.

### M1 — Data layer + Mission ingestion (2–3 days)

- SQLAlchemy models, Alembic migrations 0001–0003.
- Pydantic mission manifest loader; `pnpm validate:missions` CLI.
- Seed 1 mission (#01) end-to-end into Postgres.
- TypeScript types regenerated.
- **Exit gate:** `GET /api/v1/missions` returns Mission 01.

### M2 — Sandbox MVP (3–5 days)

- Docker base image `agentarena/node20:1` with `fullstack-auth-demo` baked in.
- `Sandbox.provision` + `run` + `read/write_file` + `diff_from_initial`.
- WebSocket PTY bridge.
- `SANDBOX_DRIVER=local` fallback for laptops without Docker.
- **Exit gate:** integration test that provisions a sandbox, runs `pnpm test:unit`, returns exit 0.

### M3 — Workspace UI v1 (4–6 days)

- Auth (magic link) wired.
- Mission catalog + detail.
- Workspace shell: file tree, Monaco, terminal, agent chat (stub), diff viewer.
- Session creation + sandbox attach.
- Manual file edit → backend → file_changes event.
- **Exit gate:** A human can open Mission 01, browse files, edit one, run tests in terminal, see the diff update.

### M4 — Agent service + Patch flow (2–3 days)

- AgentService (deterministic only — no LLM yet).
- Apply `agent_patch.diff` via `/sessions/{id}/patches/{turn_id}/apply`.
- Agent chat UI streams response, "Apply Patch" CTA.
- Supervision events for `prompt.submitted`, `agent.responded`, `patch.applied`.
- **Exit gate:** User prompts "fix the bug", agent patch applies, file tree shows changed files.

### M5 — Submission + Grading engine (4–6 days)

- Submit endpoint freezes sandbox.
- Run visible + hidden tests, all validators.
- Score engine computes rubric, persists `submissions`.
- Post-mission report page (radar + dimension breakdown + ideal solution).
- Badges awarded.
- **Exit gate:** Submit Mission 01 with the unmodified agent patch → score ~50; submit with the ideal fix → score ≥ 92.

### M6 — Remaining missions + content polish (5–8 days)

- Implement missions 02–10 (each: repo prep, manifest, agent patch, hidden tests, ideal solution).
- Tighten validators per scenario.
- Add `data-api-demo` Docker image and pin Python deps.
- **Exit gate:** All 10 missions pass the §14.11 checklist; CI `validate:missions` is green.

### M7 — Public profile + Landing + Polish (3–5 days)

- Public profile page (badges, history, radar averages).
- Shareable report URL (`/report/{id}` with OG image).
- Landing page (Hero, How It Works, Sample Report, Scenario carousel, CTA).
- Accessibility pass.
- Telemetry: PostHog or OpenTelemetry traces; basic dashboards.
- Loading/error states everywhere.
- **Exit gate:** A first-time visitor can land → understand → sign up → start Mission 01 → submit → share report → see profile.

### M8 — Hardening + ship (2–4 days)

- Load test the sandbox pool (10 concurrent sessions per box).
- Rate limits on `/sessions` and `/prompts`.
- Abuse detection (per-IP throttle, banned-prompt blocklist).
- Idle-session reaper (30 min inactivity).
- Backup + restore runbook.
- Status page (StatusPage.io or `/status` route).
- **Exit gate:** k6 script holds 25 RPS for 10 min with p95 < 800 ms on hot endpoints.

Total budget: ~26–42 dev-days. Sequence assumes single-track agent; multiple workers can parallelize M3/M4 and M5 partially.

### Post-MVP — P0 batch (post-launch hardening)

> **Note:** this table is stale; trails the codebase. The "Status" /
> "Evidence" columns are hand-maintained and may lag the actual ship
> state by a commit or two — when in doubt the file paths in the
> Evidence column are the load-bearing source of truth.

Milestones M0–M8 land the MVP product. The follow-up work is tracked in [FEATURE_GAPS.md](FEATURE_GAPS.md) and broken into batches under [P0_DESIGN.md](P0_DESIGN.md) (items 1–6) and [P0_DESIGN_11_13.md](P0_DESIGN_11_13.md) (items 11–13). Shipped status as of 2026-05-25:

| Item | Title | Status | Evidence |
|---|---|---|---|
| P0-1 | In-product onboarding (Mission 00) | ✅ shipped | commit `916d660` |
| P0-2 | Mission post-mortem walkthrough | ✅ shipped | commit `916d660` |
| P0-3 | Replay/retry mechanic + multi-attempt policy | ✅ shipped | commit `7aac383`, [ADR 0009](docs/adr/0009-multi-attempt-policy.md) |
| P0-4 | Give-up affordance with capped reveal | ✅ shipped | commit `7aac383`, [ADR 0010](docs/adr/0010-give-up-policy.md) |
| P0-5 | Legal pages + cookie consent | ✅ shipped | commit `ff161e2` |
| P0-6 | Account self-service (change email, export, delete) | ✅ shipped | commit `ff161e2` |
| P0-7 | Identity verification via GitHub OAuth | ✅ shipped | `apps/api/app/auth/github_oauth.py`, migration `0021_github_oauth.py` |
| P0-8 | Anti-cheating posture (proctored mode) | ✅ shipped | `apps/api/app/sessions/integrity.py`, `apps/api/app/sessions/service.py` (`session.mode`), migration `0022_session_mode.py` |
| P0-9 | Find-in-files / repo-wide search | ✅ shipped | `apps/api/app/sessions/router.py` (`POST /sessions/{id}/files/search`), `apps/api/app/schemas/workspace.py` |
| P0-10 | Email deliverability fallback | ✅ shipped | `apps/api/app/auth/email.py`, `apps/api/app/observability.py` (`magic_link_email_total`) |
| P0-11 | Verifiable report artifact (PDF + signed permalink) | ✅ shipped | `apps/api/app/reports/verification.py`, `apps/web/app/verify/[submissionId]`, migration `0019_report_verification.py` |
| P0-12 | Reset-to-initial workspace | ✅ shipped | `apps/api/app/sessions/router.py` (`POST /sessions/{id}/reset`), migration `0020_session_reset_event.py` |
| P0-13 | LICENSE + CONTRIBUTING + rubric reconciliation | ✅ shipped | LICENSE Apache 2.0, CONTRIBUTING/SECURITY/CODE_OF_CONDUCT, [ADR 0011](docs/adr/0011-rubric-rebalance.md) |

P1 / P2 work is tracked in the same docs but not milestoned here.

---

## 18. Concrete File-by-File Build Order Inside Each Milestone

Below is a level-2 expansion of M1–M5 only; M6+ follow the same pattern.

### 18.1 M1 file list

1. `apps/api/app/config.py` — env-driven settings (pydantic-settings).
2. `apps/api/app/db/base.py`, `session.py` — async engine + DI.
3. `apps/api/app/models/{user.py, mission.py, session.py, agent_turn.py, file_change.py, command_run.py, submission.py, badge.py, supervision_event.py}`.
4. `apps/api/alembic/versions/0001_initial.py` — all tables from §6.
5. `apps/api/app/schemas/mission.py` — Pydantic mirror.
6. `apps/api/app/missions/manifest.py` — manifest parser (yaml → Pydantic).
7. `apps/api/app/missions/loader.py` — scans `/missions`, validates, upserts.
8. `apps/api/app/missions/router.py` — `GET /missions` + `GET /missions/{id}`.
9. `scripts/validate_missions.py` — CI entrypoint.
10. `missions/01-auth-cookie-expiration/mission.yaml` + repo + patch + hidden tests.

### 18.2 M2 file list

1. `infra/docker/node20.Dockerfile`.
2. `apps/api/app/sandbox/driver.py` — abstract `SandboxDriver`.
3. `apps/api/app/sandbox/docker_driver.py` — implements `provision`, `run`, etc.
4. `apps/api/app/sandbox/local_driver.py` — subprocess fallback.
5. `apps/api/app/sandbox/pool.py` — limits concurrent sandboxes.
6. `apps/api/app/ws/terminal.py` — PTY ↔ WS bridge.
7. `apps/api/tests/test_sandbox.py`.
8. `infra/scripts/build_repo_pack.sh` — bakes a frozen repo into an image tag.

### 18.3 M3 file list

1. Auth: `app/auth/{routes.py, magic_link.py, deps.py}` + frontend `/auth/sign-in`.
2. `app/sessions/{router.py, service.py}`.
3. Frontend `apps/web/app/(app)/missions/page.tsx` + components.
4. Frontend `apps/web/app/(app)/workspace/[sessionId]/page.tsx`.
5. `components/workspace/{FileTree,CodeEditor,Terminal,DiffViewer,AgentChat,Timeline,Brief,VerificationChecklist}.tsx`.
6. `stores/workspaceStore.ts`.
7. WebSocket client (`lib/ws.ts`) with auto-reconnect.

### 18.4 M4 file list

1. `app/agent/{service.py, templates.py, intents.py}`.
2. `app/agent/router.py` — `POST /sessions/{id}/prompts`, `POST /patches/.../apply`.
3. `app/agent/llm.py` — `AnthropicClient` adapter that wraps `build_anthropic_sdk_client()` (Bedrock by default — see §16.A) with prompt caching, retries, and the seed/length validators required to keep narration deterministic-enough.
4. `missions/01-auth-cookie-expiration/prompts/{response.md, reasoning.md, intents.yaml}`.
5. Frontend `AgentChat` upgraded with context chips + apply CTA.

### 18.5 M5 file list

1. `app/grading/diff.py` — unified-diff parser.
2. `app/grading/validators/{forbidden.py, scope.py, regression_test.py, deps.py, skipped_tests.py}`.
3. `app/grading/score.py` — rubric per §11.
4. `app/grading/runner.py` — orchestrates: tests → validators → score → persist.
5. `app/sessions/submit.py` — endpoint.
6. Frontend `app/(app)/report/[submissionId]/page.tsx` + `components/report/*`.
7. `migrations/0002_seed_badges.py`.

---

## 19. Testing Strategy

### 19.1 Test pyramid

- **Unit (Pytest, Vitest)** — heaviest layer. Pure functions: scoring, validators, manifest parser, intent classifier.
- **Integration (Pytest)** — DB + sandbox + grading. Spin up a real Postgres + a real Docker sandbox in CI.
- **End-to-end (Playwright)** — smoke flows: sign in → start mission → prompt → submit → see report. Run on every PR for Mission 01 only; nightly for all 10.
- **Mission self-tests** — for each mission, an automated suite asserts:
  - Unmodified agent patch yields score in `[acceptance.min_unmodified, acceptance.max_unmodified]` (e.g. 40–60).
  - Ideal solution yields score ≥ `acceptance.min_ideal` (e.g. 92).
  - Empty submission yields score ≤ 15.

These self-tests live in `missions/<id>/acceptance.yaml`:

```yaml
acceptance:
  min_unmodified: 35
  max_unmodified: 60
  min_ideal: 92
```

### 19.2 Determinism tests

A nightly job replays the seeded "ideal" event stream for each mission 5 times and asserts identical score reports.

### 19.3 Performance tests

- `pnpm test:hidden` p95 < 30s per mission.
- Submit-to-report median < 60s.
- Workspace cold start (provision → ready) < 25s.

### 19.4 Security tests

- Sandbox escape attempts (mounted-readonly checks, no network).
- CSP headers on all HTML responses.
- Auth: token replay, expired token, swapped-cookie tests.

---

## 20. Observability

- Structured JSON logs (loguru in Python; pino in any Node sidecars).
- Trace context propagated via `traceparent` from frontend → API → worker.
- Metrics (Prometheus exposition at `/metrics`):
  - `sessions_active`, `sessions_provision_seconds`, `submissions_total`, `submissions_score_histogram`, `agent_responses_total`, `agent_llm_fallback_total`.
- Dashboards in Grafana (compose stack in dev).

Privacy: prompts and user code are sensitive. Logs MUST redact prompt text by default in prod; full prompts are stored only in DB.

---

## 21. Security & Abuse

- Sandbox: rootless Docker, dropped caps (`--cap-drop=ALL`), no host mounts, no network, seccomp profile.
- Per-user concurrency cap: 1 active session at a time (MVP).
- Hourly limits: 20 prompts, 50 commands, 3 submissions per user.
- Banned commands intercepted client-side (warning) and server-side (rejected with 400):
  - `rm -rf /`, `:(){:|:&};:`, `curl ... | sh`, `wget ... | bash`.
- LLM prompts containing prompt-injection attempts ("ignore the previous instructions") are flagged but not blocked — useful for safety-awareness scoring later.

---

## 22. Deployment

- **Web (Next.js):** Fly.io app, 1 region (`iad`), 2 machines, 256 MB.
- **API (FastAPI):** Fly.io app, 2 machines, 1 GB.
- **Workers (RQ):** Fly.io app, 2 machines, 1 GB.
- **Sandbox host:** Fly Machines worker pool (CPU-heavy plan), or Hetzner CX31 fallback. Sandboxes spawn ephemerally via Docker.
- **DB:** Fly Postgres (or Neon for serverless).
- **Redis:** Upstash (TLS).
- **Object storage:** Cloudflare R2.
- **DNS:** Cloudflare; `arena.<domain>`.
- **Secrets:** Fly secrets; never checked in. Rotation runbook in `docs/runbooks/rotate-secrets.md`.

CI deploys on merge to `main` after green pipeline + manual approval gate for `production`.

---

## 23. ADRs (Architecture Decision Records)

`docs/adr/` is the canonical index ([docs/adr/README.md](docs/adr/README.md)). The shipped list:

1. [`0001-tech-stack.md`](docs/adr/0001-tech-stack.md) — why Next.js + FastAPI + Docker.
2. [`0002-deterministic-agent.md`](docs/adr/0002-deterministic-agent.md) — why hybrid simulation over pure LLM.
3. [`0003-event-sourced-supervision.md`](docs/adr/0003-event-sourced-supervision.md) — why a single event log drives grading + UI.
4. [`0004-mission-manifest-vs-code.md`](docs/adr/0004-mission-manifest-vs-code.md) — content as YAML for editability.
5. [`0005-sandbox-isolation.md`](docs/adr/0005-sandbox-isolation.md) — Docker rootless + no-network default.
6. [`0006-scoring-rubric.md`](docs/adr/0006-scoring-rubric.md) — weighted-30 over flat-15.
7. [`0007-bedrock-llm-provider.md`](docs/adr/0007-bedrock-llm-provider.md) — Anthropic via AWS Bedrock; see §16.A.
8. [`0008-sqlalchemy-vs-prisma.md`](docs/adr/0008-sqlalchemy-vs-prisma.md) — SQLAlchemy 2.x async over Prisma.
9. [`0009-multi-attempt-policy.md`](docs/adr/0009-multi-attempt-policy.md) — public aggregates use best-per-mission; attempt count private.
10. [`0010-give-up-policy.md`](docs/adr/0010-give-up-policy.md) — give-up caps total at 50/100 via `score_cap_reason`; dimensions stay honest.
11. [`0011-rubric-rebalance.md`](docs/adr/0011-rubric-rebalance.md) — verification 20→15, diff_minimality 5→10; the §11.1 table mirrors this.

---

## 24. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sandbox escape | Low | Critical | Rootless, seccomp, no net, code review for every change to sandbox driver |
| LLM nondeterminism leaking into grading | Med | High | LLM never on grading path; agent patches deterministic; output validation + fallback |
| Mission content not believable | Med | High | Each mission peer-reviewed; agent patches reviewed for "plausibility" |
| Hidden tests too easy or too hard | Med | Med | Mission self-tests assert min/max score envelopes |
| Workspace too slow on cold start | Med | Med | Pre-warm sandbox pool of 3 idle containers per popular mission |
| Prompt-injection used to manipulate score | Low | Med | Scoring is pure functional over events, not over agent text |
| Users find a shortcut to read hidden tests | Med | High | Hidden tests mounted only at submit; container snapshot before mount |
| Cost overrun from LLM narration | Low | Low | Disabled by default; prompt-cached when on; Bedrock spend monitored via CloudWatch alarm on the bearer-token usage |
| Bedrock credential leak | Low | Critical | `keys.md` gitignored; bearer rotated quarterly; prod uses Fly secrets, not files; CI scans for the `ABSK` prefix |
| Bedrock regional outage | Low | Med | App degrades to deterministic templates automatically (LLM is optional); future: add `us-west-2` failover |

---

## 25. Definition of Done — MVP

Ship when **all** of these are true:

- [ ] 10 missions pass mission self-tests on CI.
- [ ] Landing → signup → start → submit → report flow works end-to-end for a first-time user (Playwright passes nightly).
- [ ] Sandbox can run 25 concurrent sessions for 10 minutes without OOM.
- [ ] Score determinism test: replay yields identical reports 5/5 runs across all missions.
- [ ] Accessibility audit: zero critical Axe violations on workspace + report pages.
- [ ] Security checklist (§21) signed off; sandbox escape attempts in CI pass.
- [ ] Backup + restore runbook tested.
- [ ] Public profile page renders for at least 3 demo users with realistic histories.
- [ ] README walks a contributor from `git clone` to running locally in < 15 min.

---

## 26. Out of Scope (Explicitly)

Anything in §16 of the product definition is out of scope. Additionally for MVP:

- User-authored missions.
- Real-time multiplayer or pair supervision.
- Mobile-optimized workspace (read-only OK; editing is desktop-only).
- LLM-based grading on the hot path.
- Browser-based code execution (no WASM sandbox).
- Internationalization beyond English.

---

## 27. Open Questions (Capture, Don't Block)

Tracked in [`docs/open-questions.md`](docs/open-questions.md). Status snapshot:

- **OQ-0001** — should partial credit be revealed during the mission (live `ScorePreview`) or hidden entirely until submit? *Open;* current plan reveals **process** signals only (no hidden-test outcomes, no failure-mode hints, no predicted total). See §13.5.
- **OQ-0002** — should "give up" let users see the ideal solution at a score cap of 50? *Resolved 2026-05-23 by* [ADR 0010](docs/adr/0010-give-up-policy.md). Yes, 10-min soft block, 50/100 cap via `score_cap_reason`, no hiding from the public profile.
- **OQ-0003** — pricing: free with rate limits or paid tier for unlimited replays? *Open;* free during MVP + 90-day beta, paid-tier decision deferred to post-≥1000-graded-submissions data.
- **OQ-0004** — multi-attempt strategy: best score, latest, or both? *Resolved 2026-05-23 by* [ADR 0009](docs/adr/0009-multi-attempt-policy.md). Public aggregates use best-per-mission; private mission detail surfaces count + best + latest + delta; attempt count is never public.

Resolve remaining open items before public beta.

---

## 28. Glossary of Files a Coding Agent Will Create Most Often

- `mission.yaml`, `agent_patch.diff`, `hidden_tests/*`, `forbidden_changes.yaml`, `ideal_solution.md`, `acceptance.yaml`, `prompts/*.md` per mission.
- New SQLAlchemy model + Alembic migration whenever a domain noun arrives.
- New validator under `app/grading/validators/` whenever a scenario invents an anti-pattern.

---

## 29. Quick-Reference Checklists

### 29.1 New mission checklist

```
[ ] Create missions/NN-id/ folder
[ ] Author mission.yaml (validates against schema)
[ ] Bake or reuse repo pack image
[ ] Write agent_patch.diff (applies cleanly, fails ≥1 hidden test)
[ ] Write hidden_tests/ that exercise the failure mode
[ ] Write forbidden_changes.yaml
[ ] Write ideal_solution.md
[ ] Write prompts/response.md, prompts/reasoning.md, prompts/intents.yaml
[ ] Set expected_diff_lines_p50
[ ] Add acceptance.yaml (min/max envelopes)
[ ] Add row to mission seed migration
[ ] Add to MissionCatalog test list
```

### 29.2 New endpoint checklist

```
[ ] Pydantic request/response models
[ ] Router function with auth dependency
[ ] DB transaction boundary explicit
[ ] Event emission (supervision_events) where relevant
[ ] OpenAPI examples
[ ] Unit + integration test
[ ] Frontend client regenerated
```

### 29.3 PR review checklist

```
[ ] Tests added/updated
[ ] Migrations include up + down
[ ] No secrets in diff
[ ] Determinism preserved (no time.time(), random without seed in graded code paths)
[ ] Telemetry events added for new user actions
[ ] Accessibility for new UI (axe pass)
```

---

## 30. Appendix A — Example Agent Patch (Mission 01)

```diff
--- a/backend/middleware/requireAuth.ts
+++ b/backend/middleware/requireAuth.ts
@@ -10,7 +10,7 @@ import { parseSessionCookie } from "../auth/session";
 export function requireAuth(req: Request, res: Response, next: NextFunction) {
   const raw = req.cookies?.session;
-  if (!raw) {
+  if (!raw || raw.length === 0) {
     return res.redirect("/login");
   }
   const session = parseSessionCookie(raw);
```

Note: no call to `session.isValid()`. Hidden test creates an expired session and asserts a redirect.

## 31. Appendix B — Example Ideal Solution (Mission 01)

```diff
--- a/backend/middleware/requireAuth.ts
+++ b/backend/middleware/requireAuth.ts
@@ -10,9 +10,12 @@ import { parseSessionCookie } from "../auth/session";
 export function requireAuth(req: Request, res: Response, next: NextFunction) {
   const raw = req.cookies?.session;
-  if (!raw) {
+  if (!raw) {
     return res.redirect("/login");
   }
   const session = parseSessionCookie(raw);
+  if (!session || !session.isValid(Date.now())) {
+    return res.redirect("/login");
+  }
   req.session = session;
   next();
 }
```

Plus a regression test:

```ts
it("redirects when the session cookie is expired", async () => {
  const expired = signSession({ userId: "u1", exp: Date.now() - 1000 });
  const res = await request(app).get("/dashboard").set("Cookie", `session=${expired}`);
  expect(res.status).toBe(302);
  expect(res.headers.location).toBe("/login");
});
```

## 32. Appendix C — Example Score Report Narrative

```
Score: 78 / 100

You correctly identified the auth middleware as the right place to fix and selected
the relevant context up front. You asked the agent for a regression test, which is
the highest-leverage supervision habit in this scenario.

Where you lost points:
- You accepted the agent's patch without re-running the auth test suite (Verification −6).
- The agent's patch added a presence check but skipped expiration validation. A diff
  read would have surfaced that `session.isValid()` was never called (Agent Review −4).
- Hidden test "session refresh respects expiration" failed (Final Correctness −6).

Badges earned: regression-test-writer.
Recommended next: Mission 05 — Security Validation Removed.
```

---

End of plan. Treat sections 5, 6, 7, 9, 10, 11, 14, and 17 as load-bearing — changes there cascade into the rest of the document and require updating the affected sections atomically.
