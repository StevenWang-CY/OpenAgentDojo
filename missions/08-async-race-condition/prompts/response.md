{# Mission 08 — Agent response template. Reaches for asyncio.Lock. -#}
Thanks — let me take a look.

> {{ prompt_summary }}

I read through {{ context_summary }}. The race is between two
callers entering `claim_job` simultaneously, both reading
`status=pending`, and both proceeding. The simplest serialisation
primitive is an `asyncio.Lock` wrapped around the read + write so
only one coroutine at a time can be in the critical section.

### What I'm changing

A small patch to `claim_job` — wrap the `get` + `update` in an
`asyncio.Lock` so the check and the act happen under mutual exclusion:

```diff
+    lock = asyncio.Lock()
+    async with lock:
+        job = await session.get(Job, job_id)
+        if job is None or job.status != "pending":
+            return False
+        ...
```

This keeps the existing structure of the function intact and
addresses the race surgically.

### Why I'm *not* changing the SQL

The current UPDATE is fine; the issue is purely about ordering of
concurrent coroutines on the same job id. Locking at the application
boundary is the right level of abstraction — it doesn't tie us to
any particular database's locking semantics.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the single-worker happy path still
  passes.
- Spot-check with two concurrent `await process_job(...)` calls — the
  second should come back `claimed=False`.

I'll apply this when you click **Apply Patch**.
