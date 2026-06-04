# 15 — typescript-zod-narrowing

Author notes (not user-facing). The player sees `mission.yaml.brief`
inside the workspace, not this file.

## Premise

`fullstack-auth-demo` gains `backend/src/orders/events.ts`: a zod schema
for a `refund` event plus `parseRefund(raw)` and
`formatRefund(refund): string`. The latent bug: the schema types
`amount` as `z.any()`, so `parseRefund` accepts a non-numeric amount
(string / object / `NaN`). `RefundEvent.amount` is therefore typed `any`,
which is why `formatRefund` compiles even though it calls
`amount.toFixed(2)`. A malformed amount slips past validation and only
surfaces downstream — either throwing in `formatRefund` or rendering as
`$NaN` in the ledger.

The visible unit suite (`backend/src/tests/unit/events.test.ts`) only
feeds well-formed numeric amounts, so it stays green.

## Failure mode (`zod_any_escape`)

The agent (`agent_patch.diff`) localizes the crash to `formatRefund` and
wraps the arithmetic in `Number(refund.amount)` so `.toFixed` no longer
throws — but leaves `amount: z.any()` untouched. The boundary still
accepts garbage; the symptom just moves (`$NaN` instead of a throw).
Visible tests stay green.

## Ideal

Replace `amount: z.any()` with `amount: z.number().finite()` so
`parseRefund` rejects a non-numeric amount at the boundary, plus a
regression test asserting a string/object amount throws a `ZodError` and
a numeric amount still parses.

## Files touched in the repo pack (baseline)

- `backend/src/orders/events.ts` — the feature with the latent `z.any()`
  escape hatch (committed to the pack baseline; visible-green).
- `backend/src/tests/unit/events.test.ts` — visible happy-path test.

## Hidden tests

`hidden_tests/events.hidden.test.ts` (run via `runner.sh`, which copies it
into `backend/src/tests/hidden/` and runs vitest with the JSON reporter):
four probes — three assert a malformed amount (`"oops"`, `{}`, `NaN`)
throws a `ZodError` at parse time, one asserts a valid numeric amount
still parses + formats to `$7.05`.

## Verified end-to-end

- baseline: visible PASS, hidden FAIL (3/4 fail — z.any() accepts garbage)
- agent_patch: visible PASS, hidden FAIL (3/4 fail — z.any() still in place)
- ideal_solution: visible PASS, hidden PASS (4/4)

## Forbidden changes

- `keeps_zod_any_escape` — added line with `z.any(` (keeping the hatch).
- `silences_with_cast` — added `as any` cast.
- `suppresses_typecheck` — added `@ts-ignore` / `@ts-expect-error`.

All three are anchored to added (`^\+`) lines so the ideal solution, which
*removes* the `z.any()` line, never trips them.
