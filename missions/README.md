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

| # | Folder | Kind | Status | Repo pack |
|---|---|---|---|---|
| 00 | `00-orientation` | tutorial | shipped (P0-1) | `fullstack-auth-demo` |
| 01 | `01-auth-cookie-expiration` | standard | shipped | `fullstack-auth-demo` |
| 02 | `02-agent-wrong-file` | standard | shipped | `fullstack-auth-demo` |
| 03 | `03-missing-regression-test` | standard | shipped | `fullstack-auth-demo` |
| 04 | `04-overfitted-test-fix` | standard | shipped | `data-api-demo` |
| 05 | `05-security-validation-removed` | standard | shipped | `fullstack-auth-demo` |
| 06 | `06-excessive-rewrite` | standard | shipped | `fullstack-auth-demo` |
| 07 | `07-dependency-misuse` | standard | shipped | `data-api-demo` |
| 08 | `08-async-race-condition` | standard | shipped | `data-api-demo` |
| 09 | `09-api-contract-drift` | standard | shipped | `fullstack-auth-demo` |
| 10 | `10-typecheck-ignored` | standard | shipped | `fullstack-auth-demo` |
| 11 | `11-goroutine-leak` | standard | shipped (P1-1) | `go-orders-service` |
| 12 | `12-context-cancel-dropped` | standard | shipped (P1-1) | `go-orders-service` |
| 13 | `13-error-shadowed-by-wrap` | standard | shipped (P1-1) | `go-orders-service` |
| 14 | `14-react-shop-state-desync` | standard | shipped (P1-7) | `fullstack-auth-demo` |
| 15 | `15-typescript-zod-narrowing` | standard | shipped (P1-7) | `fullstack-auth-demo` |
| 16 | `16-python-pandas-perf-trap` | standard | shipped (P1-7) | `data-api-demo` |
| 17 | `17-python-fastapi-pydantic-coercion` | standard | shipped (P1-7) | `data-api-demo` |
| 18 | `18-go-channel-deadlock-on-cancel` | standard | shipped (P1-7) | `go-orders-service` |
| 19 | `19-python-async-cancel-shielding` | standard | shipped (P1-7) | `data-api-demo` |
| 20 | `20-go-sql-transaction-leak` | standard | shipped (P1-7) | `go-orders-service` |

`kind: tutorial` ([P0_DESIGN §P0-1](../P0_DESIGN.md)) short-circuits the grader — the runner marks the user's `tutorial_completed_at` and skips scoring / badge awards. Tutorial missions are exempt from the `acceptance.yaml` envelope checks but still validate against the manifest schema.

## `_calibration/`

`missions/_calibration/<id>.yaml` envelopes are the scoring fixtures the M5 / ADR 0011 rebalance was calibrated against. They are NOT served to users — the calibration test suite at [`apps/api/app/grading/calibration.py`](../apps/api/app/grading/calibration.py) replays each envelope through the scorer to assert the rubric still hits the calibration band. Bumping the rubric requires re-running this suite and updating the envelopes if a calibration target moves.
