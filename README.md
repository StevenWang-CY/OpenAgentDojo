# AgentSupervisor Arena

A browser-based simulator that teaches developers to **supervise AI coding agents** inside real repositories. The platform grades the *process* of supervision — prompting, context selection, diff review, verification, correction, safety — not only the final patch.

> See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the engineering source-of-truth. See [CONTEXT.md](CONTEXT.md) for the domain glossary.

## What it does

1. You pick a Mission (e.g. *"Auth cookie expiration is broken"*).
2. The platform spins up a real repo in an in-browser sandbox.
3. You prompt a deliberately-flawed agent. It applies a real (but subtly wrong) patch.
4. You verify, review, correct, and submit.
5. Hidden tests + structural validators score your **supervision quality** on a 100-point rubric.
6. You earn badges and a shareable skill profile.

## Tech stack (locked)

- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript 5.6 + Tailwind 4 + shadcn/ui
- **Backend:** FastAPI (Python 3.12) + SQLAlchemy 2.x async + Alembic
- **Data:** Postgres 16, Redis 7 + RQ
- **Sandboxes:** Docker (rootless) per session, with a `local` subprocess fallback for laptops
- **Editor:** Monaco; **Terminal:** xterm.js over WebSocket
- **LLM (optional narration only):** Claude `claude-haiku-4-5` via AWS Bedrock — see [keys.md](keys.md) and [IMPLEMENTATION_PLAN.md §16.A](IMPLEMENTATION_PLAN.md)

## Quickstart (local dev)

Prereqs: `pnpm@9+`, `node@20+`, `python@3.12+`, `uv`, `docker` + `docker compose`.

### Full stack via docker compose (recommended)

```bash
cp infra/compose/.env.compose.example .env   # one-time — container-network hostnames
docker compose up                            # api, web, postgres, redis, minio, mailhog, worker
```

The API container's entrypoint runs `alembic upgrade head` and the mission
loader before booting uvicorn, so the catalog is ready by the time `/healthz`
returns 200. Open <http://localhost:3000>, hit
<http://localhost:8000/api/v1/missions> to see Mission 01.

### Manual (no docker)

```bash
# 1. Clone + install
pnpm install
cd apps/api && uv sync && cd ../..

# 2. Bring up Postgres / Redis / MinIO
pnpm compose:up

# 3. Run migrations + seed Mission 01
cd apps/api
uv run alembic upgrade head
uv run python -m app.missions.loader  # scans /missions, upserts the catalog
cd ../..

# 4. Run backend (in one terminal)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# 5. Run frontend (in another terminal)
pnpm --filter @arena/web dev
```

Then open <http://localhost:3000>. Visit <http://localhost:8000/healthz> to confirm the API is up, and <http://localhost:8000/api/v1/missions> to see the seeded mission catalog.

### Local-without-Docker fallback

If you can't run Docker, set `SANDBOX_DRIVER=local` in `apps/api/.env`. Sandboxes will run in a temp directory using `subprocess` — **no isolation; never use in prod**. A loud warning banner appears in the UI.

## Repository layout

```
apps/api/        FastAPI backend, models, sandbox orchestration, grading
apps/web/        Next.js 15 frontend (workspace, catalog, profile, landing)
missions/        Curated supervision exercises + base repo packs
infra/           Dockerfiles, docker-compose, build scripts
packages/        Shared types generated from OpenAPI
docs/            ADRs, schemas, runbooks, scenarios
```

## Tests & checks

```bash
pnpm typecheck         # all packages
pnpm lint              # all packages
pnpm test              # all packages
pnpm validate:missions # JSON-schema + structural checks on every mission
cd apps/api && uv run pytest
```

## Project status

MVP complete — milestones M0–M8 (bootstrap, data layer, sandbox, workspace UI, agent service, grading engine, mission content, public profile/landing, hardening) have all shipped. The platform now provisions sandboxes, runs the deterministic agent, grades the 100-point supervision rubric, and renders public profiles. See [IMPLEMENTATION_PLAN.md §17](IMPLEMENTATION_PLAN.md) for the milestone breakdown.

## Security

- `keys.md` is gitignored and contains the Bedrock bearer token. **Never commit it.**
- Sandboxes run rootless with `--cap-drop=ALL`, no host mounts, no network by default.
- All grading paths are deterministic — the LLM is never invoked on hot paths.
- See [docs/runbooks/rotate-secrets.md](docs/runbooks/rotate-secrets.md) for credential rotation.

## License

Internal MVP — not for redistribution.
