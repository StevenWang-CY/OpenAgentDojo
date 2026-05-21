# missions/

Curated supervision exercises. Each subfolder `NN-id/` is a single
Mission (see [CONTEXT.md](../CONTEXT.md) for the term). Folder name
carries the catalog ordering prefix; the manifest `id` field omits the
prefix (e.g. folder `01-auth-cookie-expiration/`, manifest
`id: auth-cookie-expiration`).

## Folder convention

```
missions/
├── _shared/
│   ├── repos/              # base "Repo Packs" — frozen demo repos
│   │   └── fullstack-auth-demo/
│   └── docker/             # mission-specific Docker overrides (rare)
└── NN-mission-id/
    ├── mission.yaml             # manifest (schema in docs/schemas)
    ├── agent_patch.diff         # the deterministic, deliberately-flawed patch
    ├── forbidden_changes.yaml   # anti-patterns the grader watches for
    ├── hidden_tests/            # mounted into the sandbox at submit time
    │   ├── auth.hidden.test.ts
    │   ├── package.json
    │   └── runner.sh            # entrypoint the grader invokes
    ├── ideal_solution.md        # shown post-submit
    ├── acceptance.yaml          # score envelopes for mission self-tests
    ├── prompts/
    │   ├── response.md          # agent's narrated response (Jinja2)
    │   ├── reasoning.md         # agent's chain-of-thought
    │   └── intents.yaml         # intent classifier keywords
    └── README.md                # author notes (not user-facing)
```

## Authoring a new mission

See [`IMPLEMENTATION_PLAN.md` §14.11](../IMPLEMENTATION_PLAN.md) for the
full mission-authoring checklist, and §29.1 for the quick-reference
version. Every mission MUST:

1. Validate against [`docs/schemas/mission.schema.json`](../docs/schemas/mission.schema.json).
2. Ship an `agent_patch.diff` that applies cleanly on the pinned
   `initial_commit`, passes every visible test, and fails at least one
   hidden test.
3. Ship an `ideal_solution.md` whose diff would pass every hidden test
   and every validator.
4. Define `expected_context.required` (≥ 2 files) and
   `expected_context.discouraged` (≥ 1 file).
5. Set `expected_diff_lines_p50`.

Run `pnpm validate:missions` before pushing.

## Mission inventory

| # | Folder | Status | Repo pack |
|---|---|---|---|
| 01 | `01-auth-cookie-expiration` | shipped | `fullstack-auth-demo` |
| 02 | `02-agent-edits-wrong-file` | planned | `fullstack-auth-demo` |
| 03 | `03-missing-regression-test` | planned | `fullstack-auth-demo` |
| 04 | `04-overfitted-test-fix` | planned | `data-api-demo` |
| 05 | `05-security-validation-removed` | planned | `fullstack-auth-demo` |
| 06 | `06-excessive-rewrite` | planned | `fullstack-auth-demo` |
| 07 | `07-dependency-misuse` | planned | `data-api-demo` |
| 08 | `08-async-race-condition` | planned | `data-api-demo` |
| 09 | `09-api-contract-drift` | planned | `fullstack-auth-demo` |
| 10 | `10-typecheck-ignored` | planned | `fullstack-auth-demo` |
