# Architecture Decision Records

This directory holds the load-bearing decisions for OpenAgentDojo. Each ADR is one page, follows MADR-lite, and links back to the relevant section of [IMPLEMENTATION_PLAN.md](../../IMPLEMENTATION_PLAN.md).

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [0001](./0001-tech-stack.md) | Tech Stack | Accepted | 2026-05-21 |
| [0002](./0002-deterministic-agent.md) | Deterministic Agent (Hybrid Simulation) | Accepted | 2026-05-21 |
| [0003](./0003-event-sourced-supervision.md) | Event-Sourced Supervision | Accepted | 2026-05-21 |
| [0004](./0004-mission-manifest-vs-code.md) | Mission Manifest as YAML, Not Code | Accepted | 2026-05-21 |
| [0005](./0005-sandbox-isolation.md) | Sandbox Isolation — Rootless Docker | Accepted | 2026-05-21 |
| [0006](./0006-scoring-rubric.md) | Scoring Rubric — Weighted-30 | Accepted | 2026-05-21 |
| [0007](./0007-bedrock-llm-provider.md) | Anthropic via AWS Bedrock | Accepted | 2026-05-21 |
| [0008](./0008-sqlalchemy-vs-prisma.md) | SQLAlchemy 2.x (Async) Over Prisma | Accepted | 2026-05-21 |

## Adding a new ADR

1. Copy the template from any existing ADR and renumber.
2. Status starts at `Proposed`; flip to `Accepted` when merged.
3. Supersede rather than edit — if a later ADR overrides this one, mark the old one `Superseded by ADR NNNN` and update the table.
4. Keep it to one page. If a decision needs more, write a design doc and reference it.

## Conventions

- Filenames: `NNNN-kebab-case-title.md`.
- One H1 per file (`# ADR NNNN: Title`).
- Always include: Context, Decision, Consequences (positive/negative/neutral), Alternatives considered, References.
- Cross-link related ADRs in References.
