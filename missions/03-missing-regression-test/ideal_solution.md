# Mission 03 — Ideal Solution

## Root cause

`POST /api/submissions` has no idempotency guard. The agent's `Set` is
process-local and disappears on restart; under real conditions
(multiple workers, server restarts between retries) duplicates leak
through.

The right fix lives in the *store*: enforce uniqueness on `formId`
once, where every caller — current and future — benefits. A regression
test locks the behaviour down.

## Minimal diff

```diff
--- a/backend/src/submissions/store.ts
+++ b/backend/src/submissions/store.ts
@@ -19,9 +19,15 @@ const ROWS: Submission[] = [];
 let nextId = 1;

+export class DuplicateFormIdError extends Error {
+  constructor(public formId: string) {
+    super(`duplicate formId: ${formId}`);
+    this.name = "DuplicateFormIdError";
+  }
+}
+
 export function insertSubmission(formId: string, payload: Record<string, unknown>): Submission {
+  if (ROWS.some((r) => r.formId === formId)) {
+    throw new DuplicateFormIdError(formId);
+  }
   const row: Submission = {
     id: nextId++,
     formId,
     payload,
     createdAt: Date.now(),
   };
   ROWS.push(row);
   return row;
 }
```

```diff
--- a/backend/src/routes/submissions.ts
+++ b/backend/src/routes/submissions.ts
@@ -1,6 +1,6 @@ import { Router } from "express";

-import { insertSubmission, findByFormId } from "../submissions/store";
+import { DuplicateFormIdError, insertSubmission, findByFormId } from "../submissions/store";
 ...
   if (formId.length === 0) {
     return res.status(400).json({ ok: false, error: "missing_formId" });
   }

-  const row = insertSubmission(formId, payload);
+  let row;
+  try {
+    row = insertSubmission(formId, payload);
+  } catch (err) {
+    if (err instanceof DuplicateFormIdError) {
+      const existing = findByFormId(formId)[0];
+      return res.status(200).json({ ok: true, id: existing?.id, formId, deduped: true });
+    }
+    throw err;
+  }
   return res.status(201).json({ ok: true, id: row.id, formId: row.formId });
```

Plus a regression test:

```ts
// backend/src/tests/integration/submissions.test.ts
it("two submits with the same formId only create one row", async () => {
  const a = await request(app).post("/api/submissions").send({ formId: "F1", payload: {} });
  const b = await request(app).post("/api/submissions").send({ formId: "F1", payload: {} });
  expect(a.status).toBe(201);
  expect(b.status).toBe(200);
  expect(b.body.deduped).toBe(true);
  const list = await request(app).get("/api/submissions?formId=F1");
  expect(list.body.rows).toHaveLength(1);
});
```

## What the agent got wrong

- **Process-local dedup.** A `Set<string>` in module scope dies with
  the process. Restart the server and the same `formId` is "new" again.
- **No regression test.** Even when the agent's guard works in the
  happy path, nothing protects against regressions.
- **Misplaced check.** The dedup belongs in the store next to the data,
  not in the HTTP layer where every new caller has to remember to
  reimplement it.

## What a strong supervisor would have prompted

- *"Put the uniqueness guard in the store, not in the route. Imagine a
  background worker that also writes — it would skip your route's
  check."*
- *"Add a test that posts the same `formId` twice and asserts a single
  row in the store."*
- *"Imagine a server restart between the two posts. Would your guard
  still catch the duplicate?"*

## Validators a careless patch trips

- `forbidden_changes.in_memory_idempotency_only` — fires on
  `new Set<` / `new Map<`.
- `regression_test_required` — fails if no test under
  `backend/src/tests/**` mentions one of `duplicate`, `idempot`,
  `unique`, `formId`.
