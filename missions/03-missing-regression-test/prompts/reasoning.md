{# Mission 03 — reasoning template. Skips the durability question. -#}
[plan]
1. Symptom: duplicate rows for the same `formId`.
2. Add idempotency at the HTTP boundary so the client gets a stable
   id back without changes to the data layer.
3. Track seen formIds in a module-level Set; short-circuit any
   repeat with the original row.
4. Visible test stays green; the dedup is invisible on the happy path.

[diff]
- backend/src/routes/submissions.ts +9 / 0
- nothing else

[risk]
Low — additive, no behavior change for novel formIds.

[skipped]
- Did not consider what happens on server restart; the Set is in-
  process and that's fine for the foreseeable load.
- Did not add a regression test; the existing integration suite
  already exercises the happy path.
- Did not change the store; the route is the right boundary.

[expected outcome]
Visible tests stay green. Duplicate submits return 200 + deduped: true.
