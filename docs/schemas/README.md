# JSON Schemas

These schemas are the contract between mission authoring, the API, and the grader. They use **JSON Schema draft 2020-12**.

## Files

| Schema | Source-of-truth for | Consumed by |
|---|---|---|
| [mission.schema.json](./mission.schema.json) | `missions/<id>/mission.yaml` | `pnpm validate:missions` (CI), mission loader on startup |
| [forbidden_changes.schema.json](./forbidden_changes.schema.json) | `missions/<id>/forbidden_changes.yaml` | `forbidden_changes` validator at submit time |
| [acceptance.schema.json](./acceptance.schema.json) | `missions/<id>/acceptance.yaml` | Mission self-tests in CI |
| [event.schema.json](./event.schema.json) | One row of `supervision_events.payload` | Grader (input), WebSocket `/events` channel (output) |
| [score_report.schema.json](./score_report.schema.json) | `submissions.score_report` JSONB column | Report page, public profile, post-MVP analytics |

## How CI consumes them

`scripts/validate_missions.py` walks `missions/*/mission.yaml` and validates each against `mission.schema.json` using the `jsonschema` Python package. It also:

- Cross-checks that `mission.yaml.validators[].rules_file` paths exist and validate against `forbidden_changes.schema.json`.
- Verifies an `acceptance.yaml` exists and validates against `acceptance.schema.json`.
- Runs the §14.11 structural checklist (referenced files exist, `expected_diff_lines_p50` is set, etc.).

The CLI is wired into `pnpm validate:missions` and runs on every PR.

## How the runtime consumes them

- The mission loader (`apps/api/app/missions/loader.py`) re-validates every manifest at startup. A schema violation aborts startup.
- The grader treats `event.schema.json` as advisory only — the Pydantic models in `apps/api/app/schemas/events.py` are the runtime check.
- `score_report.schema.json` is asserted in unit tests against the output of `apps/api/app/grading/score.py`.

## How the frontend consumes them

For now, the frontend imports hand-written TypeScript types from `packages/shared-types/`. Once CI is fully wired (M5), `openapi-typescript` regenerates these from FastAPI's `/openapi.json`. The JSON Schemas above are the spec; the OpenAPI is the runtime contract.

## Editing

- Bump `$id` only on breaking changes (rename a field, change required-ness in a backwards-incompatible way).
- Adding a new `oneOf` branch to `event.schema.json` is additive and safe.
- Changing `scoring_weights` constants in `mission.schema.json` is a breaking change to the rubric — see [ADR 0006](../adr/0006-scoring-rubric.md) before touching.
