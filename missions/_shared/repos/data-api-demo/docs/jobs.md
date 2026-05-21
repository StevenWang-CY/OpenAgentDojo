# Job queue semantics

`app.jobs.process_job` is the queue processor. Its contract is **exactly
one** worker may transition any given job from `pending` to `running`,
even when many workers race for the same id at the same time.

## State machine

```
   ┌─────────┐    process_job (wins)    ┌──────────┐    finish_job    ┌──────┐
   │ pending │ ───────────────────────▶ │ running  │ ───────────────▶ │ done │
   └─────────┘                          └──────────┘                  └──────┘
        │                                     │
        │ process_job (loses)                 │ failure
        ▼                                     ▼
   (no change)                            ┌────────┐
                                          │ failed │
                                          └────────┘
```

## Why the atomic UPDATE matters

The naive approach — SELECT the row, branch on `status == "pending"`,
UPDATE to `running` — has a TOCTOU window where two workers both observe
`pending`, both write `running`, and both proceed to do the work twice.

The shipped implementation closes the window with a single transactional
UPDATE filtered on `status == "pending"`. The database guarantees row-
level exclusivity; we inspect `rowcount` to find out whether *we* were
the worker that flipped it.

## Anti-patterns

- Reading the row before the UPDATE.
- Releasing the transaction between read and write.
- Looping with sleeps to "retry until claimed".
- Adding application-level locks (Redis, in-memory mutex) — those
  break across processes.
