<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/web/public/logo-dark.svg">
    <img src="apps/web/public/logo.svg" alt="OpenAgentDojo — supervisor training" width="640">
  </picture>
</p>

<p align="center">
  <strong>A browser-based simulator that teaches developers to supervise AI coding agents inside real repositories.</strong><br>
  <sub>The platform grades the <em>process</em> — prompting, context selection, diff review, verification, correction, safety — not just the final patch.</sub>
</p>

<p align="center">
  <a href="IMPLEMENTATION_PLAN.md">Implementation plan</a> ·
  <a href="CONTEXT.md">Domain glossary</a> ·
  <a href="docs/">Docs</a> ·
  <a href="missions/">Missions</a>
</p>

<p align="center">
  <img alt="Stack" src="https://img.shields.io/badge/stack-Next.js%2015%20·%20FastAPI%20·%20Postgres%2016-0A45F5?style=flat-square">
  <img alt="Language" src="https://img.shields.io/badge/typescript-5.6-13171D?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-13171D?style=flat-square">
  <img alt="Status" src="https://img.shields.io/badge/status-MVP%20complete-0A45F5?style=flat-square">
</p>

---

## How it works

1. **Pick a mission.** Each one is a real repository with a deliberately-flawed agent patch waiting for you (e.g. *"Auth cookie expiration is broken"*).
2. **Inspect, prompt, review.** The platform spins up an in-browser sandbox, you prompt the agent, it applies a patch that *looks* right but isn't.
3. **Verify and correct.** Run the visible tests. Read the diff. Push back on the agent. Submit when you're confident.
4. **Get graded on the process.** Hidden tests plus structural validators score your supervision quality on a 100-point rubric across seven dimensions.
5. **Earn badges, build a shareable skill profile.**

## Tech stack

| Layer | Choice |
| --- | --- |
| **Frontend** | Next.js 15 (App Router) · React 19 · TypeScript 5.6 · Tailwind 4 · shadcn/ui |
| **Backend** | FastAPI · Python 3.12 · SQLAlchemy 2.x async · Alembic |
| **Data** | Postgres 16 · Redis 7 + RQ |
| **Sandboxes** | Docker (rootless) per session · `local` subprocess fallback for laptops |
| **Editor / terminal** | Monaco · xterm.js over WebSocket |
| **LLM (narration only)** | Claude `claude-haiku-4-5` via AWS Bedrock — never on grading hot paths |

## Quickstart

> Prereqs: `pnpm@9+`, `node@20+`, `python@3.12+`, `uv`, `docker` + `docker compose`.

### Full stack via docker compose (recommended)

```bash
cp infra/compose/.env.compose.example .env   # one-time — container-network hostnames
docker compose up                            # api, web, postgres, redis, minio, mailhog, worker
```

The API container's entrypoint runs `alembic upgrade head` and the mission
loader before booting uvicorn, so the catalog is ready by the time `/healthz`
returns 200. Then:

- Web → <http://localhost:3000>
- API health → <http://localhost:8000/healthz>
- Mission catalog → <http://localhost:8000/api/v1/missions>

### Manual (no docker)

```bash
# 1. Install
pnpm install
cd apps/api && uv sync && cd ../..

# 2. Bring up Postgres / Redis / MinIO
pnpm compose:up

# 3. Run migrations + seed the mission catalog
cd apps/api
uv run alembic upgrade head
uv run python -m app.missions.loader  # scans /missions, upserts the catalog
cd ../..

# 4. Run backend (in one terminal)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# 5. Run frontend (in another terminal)
pnpm --filter @arena/web dev
```

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

MVP complete — milestones M0–M8 (bootstrap, data layer, sandbox, workspace UI, agent service, grading engine, mission content, public profile/landing, hardening) have all shipped. The platform provisions sandboxes, runs the deterministic agent, grades the 100-point supervision rubric, and renders public profiles. See [IMPLEMENTATION_PLAN.md §17](IMPLEMENTATION_PLAN.md) for the milestone breakdown.

## Security

- `keys.md` is gitignored and contains the Bedrock bearer token. **Never commit it.**
- Sandboxes run rootless with `--cap-drop=ALL`, no host mounts, no network by default.
- All grading paths are deterministic — the LLM is never invoked on hot paths.
- See [docs/runbooks/rotate-secrets.md](docs/runbooks/rotate-secrets.md) for credential rotation.

## License

Internal MVP — not for redistribution.

<br>

<p align="center">
  <sub>Built for engineers who'd rather review the diff than trust the demo.</sub>
</p>
