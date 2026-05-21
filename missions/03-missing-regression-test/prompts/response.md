{# Mission 03 — Agent response template. Confident, single-file fix
   that lands a process-local Set. Never mentions persistence, restarts,
   or a regression test. -#}
Thanks — I see the duplicate-rows issue. Quick summary:

> {{ prompt_summary }}

I looked through {{ context_summary }}. The handler at
`POST /api/submissions` inserts unconditionally; any retry — debounce
miss, network drop, server-side re-issue — lands a fresh row.

### What I'm changing

A small hunk in `backend/src/routes/submissions.ts`. I'm caching
`formId`s in a module-level `Set<string>` and short-circuiting any
repeat with a 200 + the original id. That gives the client an
"idempotent retry" semantics it can rely on without any data-layer
plumbing:

```diff
+const seenFormIds = new Set<string>();
...
+  if (seenFormIds.has(formId)) {
+    const existing = findByFormId(formId)[0];
+    return res.status(200).json({ ok: true, id: existing?.id, formId, deduped: true });
+  }
+  seenFormIds.add(formId);
```

### Why I'm *not* touching the store

The store is a thin write-only sink. Adding a uniqueness check there
would mean throwing exceptions out of `insertSubmission`, which the
route would have to catch — extra plumbing for the same guarantee.

### Verification I'd suggest

- Run `pnpm test:integration` to confirm the existing happy path
  still works.
- Hit the endpoint twice in a row with curl; the second response
  should come back as `deduped: true`.

I'll apply this when you click **Apply Patch**.
