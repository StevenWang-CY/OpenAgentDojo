# ADR 0004: Mission Manifest as YAML, Not Code

- Status: Accepted
- Date: 2026-05-21
- Deciders: OpenAgentDojo team

## Context

Each mission is a curated supervision exercise with many moving parts: a base repo + commit, an agent patch diff, visible/hidden tests, forbidden-change rules, expected context, scoring weights, and reward signals. We need a representation that:

1. Lets non-engineers (content authors, instructional designers) write and review missions without touching Python.
2. Can be schema-validated in CI so a broken mission fails fast.
3. Is diffable in PRs (line-level review).
4. Is replayable — the same manifest produces the same grading behavior on every run.

## Decision

Each mission is a directory `missions/<NN-id>/` containing:

- `mission.yaml` — the manifest (declarative, schema-validated).
- `agent_patch.diff` — unified diff applied by the agent service.
- `hidden_tests/` — runner-native tests mounted only at submit time.
- `forbidden_changes.yaml` — declarative anti-pattern rules.
- `ideal_solution.md` — narrative + diff shown in the post-mission report.
- `prompts/` — Jinja2 templates for the agent's prose.
- `acceptance.yaml` — score envelopes for mission self-tests.

The manifest is parsed by a Pydantic model (`apps/api/app/missions/manifest.py`) and validated against [docs/schemas/mission.schema.json](../schemas/mission.schema.json) in CI via `pnpm validate:missions`.

**No mission logic lives in Python.** Validators are configured by the manifest, not coded per mission. New scenario rules either fit an existing validator kind or warrant a new validator class (used by many missions).

## Consequences

### Positive

- Content authoring is editable by anyone who can read YAML.
- A schema violation in CI is a clear, line-targeted error, not a stack trace.
- PRs that add a mission diff cleanly — reviewers see exactly what scenario changed.
- The same manifest is replayable across grader versions: bump the grader, all 10 missions re-grade identically.

### Negative

- YAML's whitespace/quoting edge cases bite occasionally; we lean on the JSON Schema to catch type errors early.
- Some scenarios *want* one-off Python logic (e.g. "this regex but only in TSX files"). We resist this — when it happens, we add a new validator kind to the catalog instead.

### Neutral

- Mission content and platform code evolve on independent cadences. M6 (content) doesn't require platform code changes.

## Alternatives considered

- **Hand-written Python mission classes** (e.g. `class AuthCookieExpirationMission(Mission): ...`). Rejected: locks content authoring to engineers, makes diffing scenarios ugly, and biases the platform toward per-mission special cases.
- **JSON manifests.** Rejected: less ergonomic for multi-line `brief` and embedded code snippets. YAML's literal block scalars (`|`) make briefs pleasant to write.
- **TOML manifests.** Considered; rejected because nested arrays-of-objects (validators, expected_context) are clunky in TOML.
- **Database-stored manifests editable via admin UI.** Deferred — useful eventually, but for MVP we want missions in git so they version with the rest of the platform.

## References

- [IMPLEMENTATION_PLAN.md §7](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §14.11](../../IMPLEMENTATION_PLAN.md)
- [docs/schemas/mission.schema.json](../schemas/mission.schema.json)
