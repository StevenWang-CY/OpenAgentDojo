{# Mission 08 — reasoning template. -#}
[plan]
1. Symptom: occasional duplicate processing of the same job_id.
2. Cause: claim_job has a check-then-act window.
3. Wrap the check + UPDATE in an asyncio.Lock to serialise concurrent
   coroutines.
4. Keep the SQL identical; only change is local to claim_job.

[diff]
- backend/app/jobs.py +9 / -6 (lock + indent)
- nothing else

[risk]
Low — purely additive in the async layer.

[skipped]
- Did not change the WHERE on the UPDATE; the existing SQL is fine.
- Did not add a regression test; the existing single-worker test
  exercises the happy path.
- Did not think about multi-process workers; the immediate ops
  report is one process.

[expected outcome]
Concurrent calls to claim_job serialise; duplicate processing stops.
