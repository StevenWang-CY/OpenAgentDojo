# OpenAgentDojo — Domain Glossary & Conventions

Single source of truth for the load-bearing nouns and conventions used across code, UI, and docs. Keep it tight — when a new term emerges, add it here first, then use it consistently.

## Nouns

- **Mission** — a curated supervision exercise. (Product doc calls them "Scenarios"; in code & DB we use **Mission**.)
- **Repo Pack** — a frozen base repository the mission runs against (e.g. `fullstack-auth-demo@v1`). Lives under [missions/_shared/repos/](missions/_shared/repos/). Three packs ship today, one per language runtime: `fullstack-auth-demo` (TypeScript/Node), `data-api-demo` (Python/FastAPI), and `go-orders-service` (Go 1.22).
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
- **Quick Open** — `Cmd/Ctrl+P` workspace file picker (P0-9). Calls `GET /sessions/{id}/files/list`; selecting a row pipes through the Zustand `setActivePath(path)` action so the editor opens the file in one render.
- **Find in Files** — `Cmd/Ctrl+Shift+F` ripgrep-backed search panel (P0-9). Calls `POST /sessions/{id}/files/search`; the backend emits a `command.run` supervision event with `category=manual` so the grader credits supervisors for actually scoping context before prompting.
- **Help Overlay** — `?` modal listing the workspace keymap + supervision tips (P0-9). Auto-opens once on a fresh device; "don't show on startup" persists to `localStorage["oad.help.suppressOnStart"]`.
- **Verified Credential** — a graded submission with `submission.verified=True`, issued only for proctored sessions (`session.mode == 'proctored'`) and signed via the verification envelope HMAC (P0-11). Rendered on the public `/verify/{id}` page with distinct chrome — the verified chip, the proctored-mode badge, and the signature footer all light up. Survives session-secret rotation because the envelope is signed with the dedicated `VERIFY_SECRET`.
- **Honor Mode Attestation** — a graded submission with `submission.verified=False`, from a self-study session (`session.mode == 'self_study'`). Carries the same score report and rubric breakdown as a Verified Credential but is visually distinct on the verify page: no proctored-mode badge, the chip reads "honor mode," and the disclosure copy explains that the result was self-reported. Both formats use the same signed envelope so the public verify page can refuse forgeries either way.

## Determinism rules

1. **Agent patches are deterministic.** Pre-written `.diff` files. No LLM on the apply path.
2. **Grading is deterministic given the event log + the prompt-judgement cache.** The score engine itself is a pure function; the prompt-quality dimension consumes pre-computed LLM-judge verdicts from the `prompt_judgements` table. The cache is the source of truth: on cache hit the model is never called, even if the underlying Claude model is upgraded. To force a rescore campaign, bump `app.grading.prompt_judge.RUBRIC_VERSION` — this changes the cache key, so old rows no longer match and the next grading run re-judges each session under the new rubric. Old rows remain for audit.
3. **LLM use on the narration path** is gated behind `features.llm_narration_enabled` and only allowed to humanize tone of pre-rendered seed text. On error or banned-token output, silently fall back to the seed. See [IMPLEMENTATION_PLAN.md §16.A](IMPLEMENTATION_PLAN.md).
4. **LLM use on the grading path** is restricted to the cached prompt judge. If the LLM is unavailable on a cold cache, the prompt-quality dimension reports `score=null` (pending) and is excluded from the total; the report surfaces a `prompt_quality_pending` signal so the user sees measurement uncertainty rather than a fabricated number.
5. **Replays are byte-identical.** Re-running the grader against the same event stream + warm judgement cache must produce the exact same `score_report`.

## Conventions

- File paths in docs are repo-relative.
- Identifiers in YAML/JSON use `snake_case`; identifiers in TypeScript use `camelCase`; types use `PascalCase`.
- Mission IDs are kebab-case with an `NN-` prefix on the folder name (e.g. `01-auth-cookie-expiration`); the manifest `id` field omits the prefix (`auth-cookie-expiration`).
- **Adding a new mission**: run `python scripts/mission-template/init.py` from the repo root. The CLI prompts for the required metadata, enforces the closed tag vocabulary in [apps/api/app/missions/manifest.py](apps/api/app/missions/manifest.py) (`_FAILURE_MODE_TAGS`, repo packs, language runtimes), and scaffolds the next-numbered `missions/<NN>-<id>/` directory from `scripts/mission-template/template/`. See [scripts/mission-template/README.md](scripts/mission-template/README.md) for the author checklist.
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
| PDF / PNG render cache (P0-11) | `report_renders` |
| Cached prompt-judge verdicts | `prompt_judgements` |
| Earned badges | `user_badges` |
| Replayable timeline | `supervision_events` |
| Cookie / analytics consent (P0-5) | `user_consents` |
| Account self-service audit log (P0-6) | `account_events` |
| Data-export jobs (P0-6) | `data_exports` |
| Magic-link issuance | `magic_link_tokens` |
| GitHub OAuth identity (P0-7) | `users.github_id` / `github_login` / `github_avatar_url` / `github_html_url` / `github_verified_at` |
| Session mode (self-study vs proctored, P0-8) | `sessions.mode` |
| Repo-pack metadata + mission tags (P1-1) | `repo_packs`, `missions.repo_pack_id` / `tags` |
| Adaptive next-mission cache (P1-2) | `user_recommendations`, `missions.expected_weak_dim` |
| Per-session scratchpad (P1-4) | `session_notes` |
| Cached LLM-generated prose (P1-1/2/4) | `llm_cache` |
| Coaching-reflection opt-out (P1-4) | `users.coaching_opt_out` |

See [IMPLEMENTATION_PLAN.md §6](IMPLEMENTATION_PLAN.md) for the original DDL, and [P0_DESIGN.md](P0_DESIGN.md) / [P0_DESIGN_11_13.md](P0_DESIGN_11_13.md) / [P1_DESIGN.md](P1_DESIGN.md) for the migrations (0011–0033) that added the rest. The migrations under [`apps/api/alembic/versions/`](apps/api/alembic/versions/) are always the runtime truth.

## Architectural decisions of record

The load-bearing decisions live in [docs/adr/](docs/adr/README.md). Of these, the ones most likely to govern day-to-day code review:

- [ADR 0002](docs/adr/0002-deterministic-agent.md) — hybrid-simulation agent; **no LLM on the grading hot path**.
- [ADR 0003](docs/adr/0003-event-sourced-supervision.md) — append-only `supervision_events` drives both timeline UI and grader.
- [ADR 0005](docs/adr/0005-sandbox-isolation.md) — rootless Docker, `--cap-drop=ALL`, `--network=none`.
- [ADR 0006](docs/adr/0006-scoring-rubric.md) + [ADR 0011](docs/adr/0011-rubric-rebalance.md) — the 100-point weighted rubric, post-rebalance (verification 15, diff_minimality 10).
- [ADR 0009](docs/adr/0009-multi-attempt-policy.md) — public aggregates use best-per-mission; attempt count is private.
- [ADR 0010](docs/adr/0010-give-up-policy.md) — give-up caps the total at 50/100 via `score_cap_reason`; dimensions remain honest.
