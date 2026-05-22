# ADR 0008: SQLAlchemy 2.x (Async) Over Prisma

- Status: Accepted
- Date: 2026-05-21
- Deciders: OpenAgentDojo team

## Context

The backend is Python (FastAPI) per [ADR 0001](./0001-tech-stack.md). The team uses Prisma on other Node projects and considered importing the habit here via `prisma-client-py`. The competing choice is SQLAlchemy 2.x (the modern async-first iteration).

## Decision

Adopt **SQLAlchemy 2.x async** + **Alembic** for migrations.

- Models live in `apps/api/app/models/` as typed dataclasses-style classes (`Mapped[...]` annotations).
- Alembic generates and applies migrations; `up` and `down` are both required per the PR checklist (§29.3).
- TypeScript types for the frontend are *not* generated from the ORM; they come from FastAPI's OpenAPI output via `datamodel-code-generator` and `openapi-typescript`.

## Consequences

### Positive

- Stable, deeply documented, native Python — no cross-language schema sync surprises.
- Async support is mature (`AsyncSession`, `AsyncEngine`) and integrates cleanly with FastAPI's `Depends`.
- Alembic's autogen catches most schema drift; full control when autogen is wrong.
- The `Mapped[...]` typing story gives us mypy/pyright coverage on queries.

### Negative

- More boilerplate than Prisma's declarative schema language.
- Alembic's autogen has known blind spots (enum changes, index renames) — covered in the PR checklist.
- Slower onboarding for engineers who've only used Prisma.

### Neutral

- We do not use SQLAlchemy's relationship loading magic at the API boundary; routes explicitly select the columns they need.

## Alternatives considered

- **Prisma (`prisma-client-py`).** Lovely DX, but `prisma-client-py` lags the JS client, and we'd still need Alembic-equivalent migration tooling.
- **SQLModel.** Built on SQLAlchemy + Pydantic; tempting because it collapses our two model layers (ORM + schema). Rejected because we want the freedom to diverge ORM and API shapes (e.g. computed fields, projection columns).
- **Tortoise ORM.** Async-native but smaller community; Alembic-equivalent (Aerich) is less mature.
- **Raw asyncpg + Pydantic.** Fastest, but every query is bespoke and refactors are painful.

## References

- [IMPLEMENTATION_PLAN.md §2](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §6](../../IMPLEMENTATION_PLAN.md)
- [ADR 0001: Tech stack](./0001-tech-stack.md)
