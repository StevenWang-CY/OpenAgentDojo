# Mission 08 — Ideal Solution

## Root cause

`claim_job` is a textbook check-then-act: it `get()`s the row, branches
on its status, then UPDATEs without re-checking the guard. Two
concurrent callers can both see `status == "pending"` and both proceed.
The right primitive is a single UPDATE with the guard *in* the WHERE
clause; the database serialises the row, and `rowcount` tells us who
won.

## Minimal diff

```diff
--- a/backend/app/jobs.py
+++ b/backend/app/jobs.py
@@ -42,15 +42,15 @@ class ProcessResult:
 async def claim_job(session: AsyncSession, job_id: int) -> bool:
-    job = await session.get(Job, job_id)
-    if job is None or job.status != "pending":
-        return False
-    stmt = update(Job).where(Job.id == job_id).values(status="running")
-    await session.execute(stmt)
+    stmt = (
+        update(Job)
+        .where(Job.id == job_id, Job.status == "pending")
+        .values(status="running")
+    )
+    result = await session.execute(stmt)
     await session.commit()
-    return True
+    return (result.rowcount or 0) == 1
```

Plus a regression test (visible suite or integration):

```python
# backend/tests/integration/test_jobs_concurrency.py
import asyncio
import pytest
from app.db import Job, init_schema, make_engine, make_session_factory
from app.jobs import process_job

async def test_only_one_of_twenty_workers_claims():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_schema(engine)
    sf = make_session_factory(engine)
    async with sf() as s:
        s.add(Job(payload="x", status="pending"))
        await s.commit()
        job_id = (await s.scalar(select(Job.id).limit(1)))
    results = await asyncio.gather(*[process_job(sf, job_id) for _ in range(20)])
    assert sum(1 for r in results if r.claimed) == 1
```

Net change: ~8 added lines + the regression test.

## What the agent got wrong

- **`asyncio.Lock` doesn't survive across processes.** Even if the
  lock were created once at module scope (the agent's patch creates a
  fresh lock per call, which doesn't help even within one process),
  the moment a second worker process or a second event loop is in
  play, the lock provides no guarantee.
- **Application-level locks don't compose with the database.** The
  database is already the synchronisation point; reach for *it*, not
  for an in-memory primitive.
- **No regression test.** The 20-way `gather` test is exactly what
  the failure mode demands.

## What a strong supervisor would have prompted

- *"Don't reach for asyncio.Lock. The database is the synchronisation
  point — use UPDATE … WHERE status='pending' and check `rowcount`."*
- *"Write a test that uses `asyncio.gather` to fire 20 concurrent
  `process_job` calls and asserts exactly one came back claimed."*
- *"What happens if two worker processes — different event loops —
  hit `claim_job` at the same moment under your patch?"*

## Validators a careless patch trips

- `forbidden_changes.introduces_asyncio_lock` — fires on
  `asyncio.Lock()`.
- `regression_test_required` — fails without a test mentioning
  `gather`, `concurrent`, `race`, or `claim`.
- Hidden test "20 concurrent workers: exactly one claims the job"
  fails for any pure-Python locking strategy.
