# @arena/shared-types

TypeScript types shared between the Next.js frontend and the FastAPI backend.

## Regeneration story

Per IMPLEMENTATION_PLAN.md §12.3, the long-term plan is for CI to:

1. Boot the FastAPI app.
2. Dump `/openapi.json` to `packages/shared-types/openapi.json`.
3. Run `openapi-typescript` to regenerate `src/api.ts`.

For M0–M2, this package ships **hand-authored** types so the frontend can be
developed and typechecked without the backend running. Every file under
`src/` carries a `// Hand-authored until CI regenerates from openapi.json`
banner so the boundary is obvious once codegen lands.

When the backend exists and the codegen step is wired into CI, replace
`src/api.ts` with the generated output and keep `src/events.ts` +
`src/mission.ts` as hand-authored extensions (event payloads and mission
manifest shapes that are not exposed verbatim over the REST API).

## Consumers

- `apps/web` — imports from `@arena/shared-types` via the tsconfig path alias.
- (future) `apps/api` integration tests may reference the OpenAPI schema for
  contract assertions.
