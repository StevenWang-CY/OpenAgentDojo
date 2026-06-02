# @arena/shared-types

TypeScript types shared between the Next.js frontend and the FastAPI backend.

## Regeneration story

Per IMPLEMENTATION_PLAN.md §12.3, the contract types are generated from the
backend's OpenAPI schema. Since M5 the pipeline is:

1. `apps/api/scripts/dump_openapi.py` boots the FastAPI app in-process and
   writes `apps/api/openapi.json`.
2. `pnpm --filter @arena/shared-types regen` runs `openapi-typescript` and
   produces `src/api.gen.ts` (do not hand-edit — the file carries an
   "AUTO-GENERATED" banner and is overwritten on every regen).
3. The pre-commit hook and the `contracts` CI workflow both regen and fail
   the build if `api.gen.ts` drifts from the committed copy, so the shared
   types always match the running backend.

`src/api.ts` is a small hand-curated re-export surface on top of `api.gen.ts`
that exposes the generated `components` map as named types and patches the
few shapes the backend expresses too loosely for the generator to capture
(notably the JSONB fields of `SubmissionRead`). Shapes the backend now
declares a Pydantic response model for — including the `GET /auth/me`
`UserRead` — are sourced directly from the generated `components` map.

`src/events.ts` and `src/mission.ts` remain hand-authored — the supervision
event payloads are serialised by the backend as untyped `dict`, and the
mission manifest fields exposed to the UI are a frontend-only subset of the
backend manifest, so neither shape can be derived mechanically from OpenAPI.

## Consumers

- `apps/web` — imports from `@arena/shared-types` via the tsconfig path alias.
- (future) `apps/api` integration tests may reference the OpenAPI schema for
  contract assertions.
