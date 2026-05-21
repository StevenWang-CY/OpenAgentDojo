# ADR 0001: Tech Stack

- Status: Accepted
- Date: 2026-05-21
- Deciders: AgentSupervisor Arena team

## Context

The MVP must ship a browser-based simulator with a real in-sandbox editor + terminal + diff view, a deterministic flawed-agent service, and a grading engine that produces byte-identical reports on replay. We need a stack that:

1. Lets us own the schema in one place and project it into typed clients on the frontend.
2. Has a first-class story for long-lived WebSocket streams (terminal PTY + supervision events).
3. Runs Docker containers as user-isolation primitives without a custom hypervisor.
4. Lets a small team move fast across both halves of the app.

## Decision

- **Frontend: Next.js 15 (App Router) + React 19 + TypeScript 5.6.** SSR for the marketing landing and public profile (good OG / share semantics); client-rendered "workspace" for the high-interaction panes.
- **Backend: FastAPI on Python 3.12.** Pydantic models are the single source of schema truth; auto-generated OpenAPI feeds the frontend's typed client.
- **Database: PostgreSQL 16** via SQLAlchemy 2.x async + Alembic (see [ADR 0008](./0008-sqlalchemy-vs-prisma.md)).
- **Sandboxes: Docker (rootless), one container per session,** with a `local` subprocess fallback for laptops where Docker isn't available (see [ADR 0005](./0005-sandbox-isolation.md)).

## Consequences

### Positive

- One language per tier; minimal context-switching.
- FastAPI's OpenAPI output drives `openapi-typescript` so the frontend never hand-rolls request types.
- Docker as the sandbox primitive means we can boot the same image locally, in CI, and in production without code changes.
- Next.js App Router gives us file-system routing for marketing + workspace under one deploy.

### Negative

- Two runtimes to maintain (Node for web, Python for API). pnpm workspaces + a shared `packages/shared-types` mitigate the cross-runtime tax.
- Docker-per-session is heavier than process-level isolation; we accept the cost in exchange for clear blast-radius semantics.
- App Router's data-loading story is still maturing; we keep server components shallow and put workspace state in Zustand.

### Neutral

- Tailwind 4 + shadcn/ui means new components are scaffolded by copy, not import — fine for an MVP, will revisit at scale.
- Redis 7 + RQ for sandbox provisioning jobs; we can swap to Celery later if we need richer scheduling.

## Alternatives considered

- **Remix instead of Next.js.** Comparable DX, but Next.js has the broader plugin ecosystem (Monaco, xterm) and Vercel-style adapters land first on Next.
- **Rails (Ruby) backend.** Excellent ergonomics, but the schema-to-typed-client story would require maintaining a parallel TypeScript declaration; Pydantic+OpenAPI is one fewer thing to drift.
- **MongoDB instead of Postgres.** Our domain (sessions, events, submissions) is intensely relational — joins, foreign-key integrity, and JSONB for the event payloads gives us the best of both worlds.
- **Firecracker microVMs instead of Docker.** Stronger isolation, but operationally heavier and slower to cold-start. Docker rootless with `--cap-drop=ALL` + no network is sufficient for MVP; we revisit if we ever expose sandboxes to untrusted code.

## References

- [IMPLEMENTATION_PLAN.md §2](../../IMPLEMENTATION_PLAN.md)
- [ADR 0005: Sandbox isolation](./0005-sandbox-isolation.md)
- [ADR 0008: SQLAlchemy vs Prisma](./0008-sqlalchemy-vs-prisma.md)
