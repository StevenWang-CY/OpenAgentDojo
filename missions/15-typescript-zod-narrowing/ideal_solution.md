# Mission 15 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the minimal
correct diff with a walkthrough of what the agent got wrong and what a
strong supervisor would have caught.

## Root cause (in one sentence)

`refundEventSchema` types `amount` as `z.any()`, so `parseRefund` accepts
*any* value — string, object, `NaN` — and `RefundEvent.amount` is typed
`any`, which is why `formatRefund` compiles while silently assuming a
number it never validated.

## Minimal diff

```diff
--- a/backend/src/orders/events.ts
+++ b/backend/src/orders/events.ts
@@ -11,10 +11,10 @@
 export const refundEventSchema = z.object({
   kind: z.literal("refund"),
   orderId: z.string(),
-  // NOTE: `amount` is loosely typed here. `z.any()` accepts whatever the
-  // upstream producer sends, which keeps the parser permissive but pushes
-  // every type assumption downstream onto callers like `formatRefund`.
-  amount: z.any(),
+  // The amount is money: it must be a finite number. Validating it here at
+  // the boundary means every downstream caller can rely on the type instead
+  // of re-checking (or crashing on) a value the producer fat-fingered.
+  amount: z.number().finite(),
 });
 
 export type RefundEvent = z.infer<typeof refundEventSchema>;
@@ -31,8 +31,8 @@
 /**
  * Render a refund as a currency string, e.g. `$12.50`.
  *
- * Assumes `amount` is a finite number — the boundary (`parseRefund`) is
- * responsible for guaranteeing that before we get here.
+ * `amount` is guaranteed numeric by `parseRefund`, so the arithmetic here
+ * is total — no defensive coercion required.
  */
 export function formatRefund(refund: RefundEvent): string {
   return "$" + refund.amount.toFixed(2);
```

Plus a regression test (visible suite) that asserts a malformed amount is
rejected at the boundary and a valid one still round-trips:

```ts
// backend/src/tests/unit/events.test.ts
import { ZodError } from "zod";

it("rejects a non-numeric refund amount at parse time", () => {
  expect(() =>
    parseRefund({ kind: "refund", orderId: "ord-1", amount: "oops" }),
  ).toThrow(ZodError);
});

it("still accepts a numeric refund amount", () => {
  const refund = parseRefund({ kind: "refund", orderId: "ord-2", amount: 3.5 });
  expect(refund.amount).toBe(3.5);
});
```

Net change: a single field narrowed from `z.any()` to `z.number()`, plus
one regression test. That's the `expected_diff_lines_p50` envelope this
mission is scored against. Narrowing the schema rejects the bad value
where it enters the system, so every downstream caller — `formatRefund`,
the ledger sum, the comparison against the original charge — can rely on
the type instead of re-validating or crashing.

## What the agent got wrong

Two red flags a supervisor should catch on the agent's diff:

1. **It fixed the formatter, not the boundary.** Wrapping the arithmetic
   in `Number(refund.amount)` stops the throw, but `Number("oops")` is
   `NaN` and `Number({})` is `NaN` — so the ledger now records `$NaN`
   instead of rejecting the event. The malformed data still got past
   `parseRefund`; the symptom just moved.
2. **It left `z.any()` in place on purpose.** The `[skipped]` section of
   `prompts/reasoning.md` literally says it declined to narrow the schema
   because `z.any()` is "permissive on purpose" — that rationalization is
   the failure mode. `z.any()` widens the parsed type back to `any`, which
   is exactly why the bug exists.

## What a strong supervisor would have prompted

- *"What type does `amount: z.any()` let through `parseRefund`? Try a
  string."* — surfaces that the boundary validates nothing.
- *"Narrow the schema so a non-numeric amount is rejected at parse time,
  then add a test that asserts it throws."* — forces the next diff to fix
  the boundary, not the formatter.
- *"If `Number(amount)` is `NaN`, what does the ledger record?"* — exposes
  that the coercion just launders bad data into `$NaN`.

## Validators a careless patch would still trip

- `regression_test_required` — no new test mentions `parseRefund` /
  `z.number` / `ZodError` / `amount`.
- `forbidden_changes.keeps_zod_any_escape` — fires on any patch that keeps
  or re-adds a `z.any()` field instead of narrowing it.
- `forbidden_changes.silences_with_cast` / `suppresses_typecheck` — fire
  on an `as any` cast or a `@ts-ignore` that hides the type hole instead
  of validating the value.
