# Onboarding — Welcome, New Contributor

Goal: from `git clone` to running the platform locally in **15 minutes or less**. If you hit a 15-minute wall, please open an issue tagged `onboarding-friction`. Onboarding is a product.

## Prereqs

Install (versions are minimums):

- `pnpm` 9+
- `node` 20+
- `python` 3.12+
- `uv` (Python package manager — `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `docker` + `docker compose` (Docker Desktop on macOS/Windows; native on Linux)

Sanity-check:

```bash
pnpm --version && node --version && python3 --version && uv --version && docker --version
```

If you cannot install Docker (locked-down corp laptop), see the [no-Docker fallback](#no-docker-fallback) below — you'll still get a working dev loop.

## 15-minute path

```bash
# 1. Clone
git clone https://github.com/StevenWang-CY/OpenAgentDojo.git
cd OpenAgentDojo

# 2. Install JS + Python deps
pnpm install
cd apps/api && uv sync && cd ../..

# 3. Bring up Postgres / Redis / MinIO
pnpm compose:up

# 4. Migrate and seed Mission 01
cd apps/api
uv run alembic upgrade head
uv run python -m app.missions.loader   # scans /missions and upserts the catalog
cd ../..

# 5. Run the API (terminal A)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# 6. Run the web app (terminal B)
pnpm --filter @arena/web dev
```

Open [http://localhost:3000](http://localhost:3000). You should see the mission catalog. Visit [http://localhost:8000/healthz](http://localhost:8000/healthz) to confirm the API is up, and [http://localhost:8000/api/v1/missions](http://localhost:8000/api/v1/missions) for the seeded catalog.

## Env setup

Copy `.env.example` to `apps/api/.env`. The defaults work for local dev. For LLM-narration locally (optional), copy the values from [`keys.md`](../keys.md) into `apps/api/.env`:

```
ANTHROPIC_PROVIDER=bedrock
AWS_BEARER_TOKEN_BEDROCK=<value from keys.md>
AWS_REGION=us-east-2
```

`keys.md` is gitignored — don't move it into a tracked file.

## No-Docker fallback

If you can't run Docker, the sandbox layer degrades to a `subprocess` runner. Set in `apps/api/.env`:

```
SANDBOX_DRIVER=local
```

The UI shows a loud red banner whenever this driver is active. **Never enabled in prod** (the config validator rejects it when `ARENA_ENV=production`).

## Where to find things

| You want | Look in |
|---|---|
| Engineering source-of-truth | [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) |
| Domain glossary | [CONTEXT.md](../CONTEXT.md) |
| Contribution guide | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| Architecture decisions | [docs/adr/README.md](./adr/README.md) |
| JSON Schemas (mission, events, score reports) | [docs/schemas/README.md](./schemas/README.md) |
| API reference | [docs/api.md](./api.md) |
| How scoring works | [docs/grading.md](./grading.md) |
| Operational playbooks | [docs/runbooks/README.md](./runbooks/README.md) |
| Mission design notes | [docs/scenarios/README.md](./scenarios/README.md) |
| Security posture | [docs/security.md](./security.md) — disclosure path in [SECURITY.md](../SECURITY.md) |
| Feature-gap audit + P0 designs | [FEATURE_GAPS.md](../FEATURE_GAPS.md), [P0_DESIGN.md](../P0_DESIGN.md), [P0_DESIGN_11_13.md](../P0_DESIGN_11_13.md) |
| Open product/eng questions | [docs/open-questions.md](./open-questions.md) |

## Day-1 reading list

If you have an hour, read in this order:

1. [README.md](../README.md) (5 min).
2. [CONTEXT.md](../CONTEXT.md) (5 min) — get the nouns.
3. [IMPLEMENTATION_PLAN.md §1–§7](../IMPLEMENTATION_PLAN.md) (25 min) — north-star, stack, data model, mission manifest.
4. [docs/adr/0002-deterministic-agent.md](./adr/0002-deterministic-agent.md), [0003-event-sourced-supervision.md](./adr/0003-event-sourced-supervision.md), [0006-scoring-rubric.md](./adr/0006-scoring-rubric.md) (15 min) — the load-bearing decisions.
5. [docs/grading.md](./grading.md) (10 min) — to know what the platform actually measures.

## Conventions

- File paths in docs are repo-relative.
- YAML/JSON keys: `snake_case`. TypeScript: `camelCase`. Types: `PascalCase`.
- Mission folders: `NN-kebab-case-id/`. The manifest `id` drops the `NN-` prefix.
- Times: `TIMESTAMPTZ` in DB, ISO-8601 UTC in JSON.
- Commit messages: imperative mood, ≤ 72 chars for the subject, blank line then body.
- LLM access **only** via `civitas_core.llm.anthropic_client.build_anthropic_sdk_client()`. Never hard-code Bedrock profile ids.
- Never commit a file mentioning the `ABSK` token prefix.

## Workspace keyboard shortcuts (P0-9)

The workspace registers a small global keymap at the document level (capture phase so Monaco's bindings don't swallow them). Press `?` inside the workspace at any time for the full table.

| Shortcut | Action |
|---|---|
| `Cmd/Ctrl+P` | Quick open file — fuzzy-filter the workspace tree from anywhere |
| `Cmd/Ctrl+Shift+F` | Find in files — ripgrep across the sandbox; results stream into a side panel |
| `Cmd/Ctrl+Enter` | Submit the current prompt to the agent |
| `Cmd/Ctrl+S` | Save the active file (debounced auto-save also runs in the background) |
| `Esc` | Close the topmost overlay |
| `?` | Toggle the help overlay |

Both search surfaces are server-backed (`GET /sessions/{id}/files/list`, `POST /sessions/{id}/files/search`). The search endpoint emits a `command.run` supervision event with `category=manual` so the grader can credit careful context-scoping in the `context_selection` dimension.

## How to add a mission

1. Copy [docs/scenarios/template.md](./scenarios/template.md) into `docs/scenarios/<NN>-<id>.md` and draft the design note. Get sign-off before authoring the manifest.
2. Create `missions/<NN>-<id>/` and follow [IMPLEMENTATION_PLAN.md §29.1](../IMPLEMENTATION_PLAN.md):
   - Write `mission.yaml` (validates against [docs/schemas/mission.schema.json](./schemas/mission.schema.json)).
   - Bake or reuse a repo pack.
   - Write `agent_patch.diff` (applies cleanly on `initial_commit`, fails ≥1 hidden test).
   - Write `hidden_tests/`, `forbidden_changes.yaml`, `ideal_solution.md`, `prompts/`, `acceptance.yaml`.
3. Add the mission to the catalog seed migration.
4. Run `pnpm validate:missions` and `pnpm test:missions:<NN>` until green.
5. PR includes the design note + manifest + acceptance test passing in CI.

## Tests & checks

```bash
pnpm typecheck            # all TS packages
pnpm lint                 # all packages
pnpm test                 # unit + integration
pnpm validate:missions    # JSON-schema + structural checks
cd apps/api && uv run pytest
```

## Getting stuck

- Check open issues tagged `onboarding-friction` — someone may have hit this already.
- Ask in `#arena-eng` (Slack). Don't suffer in silence — onboarding friction is a bug.
- File a new `onboarding-friction` issue if the docs were wrong or missing. Your fresh eyes are valuable; capture the moment.

## References

- [README.md](../README.md)
- [IMPLEMENTATION_PLAN.md §29](../IMPLEMENTATION_PLAN.md)
- [docs/adr/README.md](./adr/README.md)
