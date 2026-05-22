# OpenAgentDojo — Domain Glossary & Conventions

Single source of truth for the load-bearing nouns and conventions used across code, UI, and docs. Keep it tight — when a new term emerges, add it here first, then use it consistently.

## Nouns

- **Mission** — a curated supervision exercise. (Product doc calls them "Scenarios"; in code & DB we use **Mission**.)
- **Repo Pack** — a frozen base repository the mission runs against (e.g. `fullstack-auth-demo@v1`). Lives under [missions/_shared/repos/](missions/_shared/repos/).
- **Session** — one user's attempt at one mission. Pairs a row in `sessions` with an ephemeral sandbox container.
- **Workspace** — the in-browser environment serving an active Session (file tree, editor, terminal, diff, chat, brief, timeline).
- **Agent Turn** — one prompt → response cycle. Each turn records the user prompt, selected context, agent response, and optional applied patch.
- **Agent Patch** — the deliberately-flawed unified diff the agent applies. **Deterministic per mission** — committed as `agent_patch.diff` in the mission folder.
- **Verification Event** — any test / typecheck / lint / manual command the user runs. Recorded in `command_runs` and emitted as `command.run` supervision events.
- **Submission** — final patch frozen at submit time. Triggers hidden tests + validators + scoring.
- **Score Report** — graded result with 7 rubric dimensions + narrative feedback.
- **Forbidden Change** — a scenario-specific anti-pattern (e.g. "remove auth middleware").
- **Failure Mode** — the category of mistake the agent makes (one per mission).
- **Supervision Quality** — composite of process metrics (prompt, context, review, verify, correct, safety, minimality).
- **Supervision Event** — append-only log row in `supervision_events`; drives both the live timeline UI and the post-hoc grader.
- **Sandbox Driver** — pluggable backend (`docker` for prod, `local` for laptops without Docker).
- **Repo Pack Image** — Docker image tag derived from a repo pack at a pinned commit (e.g. `agentarena/fullstack-auth-demo:abc123de`).

## Determinism rules

1. **Agent patches are deterministic.** Pre-written `.diff` files. No LLM on the apply path.
2. **Grading is deterministic.** Pure functions over the event log + diff + test results. No LLM on the grading path — ever.
3. **LLM use** is gated behind `features.llm_narration_enabled` and only allowed to humanize tone of pre-rendered seed text. On error or banned-token output, silently fall back to the seed. See [IMPLEMENTATION_PLAN.md §16.A](IMPLEMENTATION_PLAN.md).
4. **Replays are byte-identical.** Re-running the grader against the same event stream must produce the exact same `score_report`.

## Conventions

- File paths in docs are repo-relative.
- Identifiers in YAML/JSON use `snake_case`; identifiers in TypeScript use `camelCase`; types use `PascalCase`.
- Mission IDs are kebab-case with an `NN-` prefix on the folder name (e.g. `01-auth-cookie-expiration`); the manifest `id` field omits the prefix (`auth-cookie-expiration`).
- All Anthropic LLM access goes through `civitas_core.llm.anthropic_client.build_anthropic_sdk_client()` (Bedrock by default in prod). **Never hard-code Bedrock inference-profile ids.**
- All POST routes require `X-Csrf-Token` once auth is enabled (M3+).
- Times are `TIMESTAMPTZ` in DB and ISO-8601 UTC in JSON.

## File layout (high level)

- [apps/api/](apps/api/) — FastAPI backend, SQLAlchemy models, sandbox orchestration, grading.
- [apps/web/](apps/web/) — Next.js 15 App Router frontend.
- [missions/](missions/) — normative content: repo packs + per-mission manifests, patches, tests.
- [infra/](infra/) — Dockerfiles, compose stacks, build scripts.
- [packages/shared-types/](packages/shared-types/) — TypeScript types generated from the FastAPI OpenAPI schema.
- [docs/](docs/) — ADRs, schemas, runbooks, scenarios.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — the engineering source-of-truth.
- [keys.md](keys.md) — local secrets, **gitignored**, never commit.

## Where things live in the DB

| Concept | Table |
|---|---|
| User identity | `users` |
| Mission catalog | `missions` |
| One attempt | `sessions` |
| Each turn | `agent_turns` |
| User edits & reverts | `file_changes` |
| Shell commands | `command_runs` |
| Final patch + report | `submissions` |
| Earned badges | `user_badges` |
| Replayable timeline | `supervision_events` |

See [IMPLEMENTATION_PLAN.md §6](IMPLEMENTATION_PLAN.md) for full DDL.
