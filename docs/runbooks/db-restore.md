# Runbook: Backup & Restore Postgres

This runbook covers our Postgres backup posture and the step-by-step restore from a backup. Tested as part of the M8 "Definition of Done" checklist.

## What we back up

- **Logical dumps** of `arena` database, nightly at 03:10 UTC. `pg_dump --format=custom`, retained 14 days.
- **WAL archiving** to Cloudflare R2 every 60 s, retained 7 days. Enables point-in-time recovery (PITR).
- **Schema snapshot** (`pg_dump --schema-only`) committed monthly to `infra/db/snapshots/` for human-readable change tracking.

We do **not** back up the sandbox containers (ephemeral by design), Redis (queue state — losing it costs only in-flight provisioning jobs), or the object-storage bucket (R2 has its own versioning).

## Restoring

### Choose your scenario

| Scenario | Action |
|---|---|
| Lost the prod DB entirely | Provision a new Fly Postgres, restore latest nightly, replay WAL to target time |
| Reverting a bad migration | Roll forward — write a new Alembic migration; do NOT restore |
| Corrupted single table | Restore the table from a nightly into a scratch DB, copy rows back |
| Dev / staging refresh from prod | Use a redacted dump (next section) |

### A. Full restore from nightly dump

1. **Stop writes.** Scale `arena-api` to 0:
   ```bash
   fly scale count 0 --app arena-api
   ```
   This drops new requests cleanly and lets in-flight grading finish.

2. **Snapshot the current (broken) DB** before you destroy anything:
   ```bash
   fly postgres connect --app arena-db -- pg_dump --format=custom > broken.dump
   ```
   This is your safety net — name it with a timestamp.

3. **Locate the target dump in R2:**
   ```bash
   aws --endpoint-url=$R2_ENDPOINT s3 ls s3://arena-backups/pg/
   # filenames look like 2026-05-20T03-10-00Z.dump
   aws --endpoint-url=$R2_ENDPOINT s3 cp s3://arena-backups/pg/<file>.dump ./restore.dump
   ```

4. **Restore into a fresh DB** (we don't restore in-place):
   ```bash
   fly postgres create --name arena-db-restore --region iad
   fly postgres connect --app arena-db-restore -- psql -c "CREATE DATABASE arena;"
   fly postgres connect --app arena-db-restore -- pg_restore --no-owner --no-acl --dbname=arena < restore.dump
   ```

5. **Verify**:
   ```bash
   fly postgres connect --app arena-db-restore -- psql arena -c \
     "SELECT count(*) FROM users; SELECT count(*) FROM sessions; SELECT count(*) FROM submissions;"
   ```
   Cross-reference counts against pre-incident monitoring.

6. **Cut over DNS / connection strings.** Update `DATABASE_URL` for `arena-api` and `arena-workers`:
   ```bash
   fly secrets set DATABASE_URL=<new-uri> --app arena-api
   fly secrets set DATABASE_URL=<new-uri> --app arena-workers
   ```

7. **Scale back up:**
   ```bash
   fly scale count 2 --app arena-api
   ```

8. **Decommission the old DB only after 24 hours** of successful operation on the new one.

### B. Point-in-time recovery (PITR)

If the bad change happened mid-day, we want to replay WAL up to the second before. Fly Postgres exposes this as a managed flow:

```bash
fly postgres restore --app arena-db --restore-target-time "2026-05-20T14:32:00Z" --new-app arena-db-pitr
```

Then follow steps 4–8 of the full restore using `arena-db-pitr` as the source.

### C. Single-table recovery

1. Restore the nightly into a scratch DB (above, steps 3–4).
2. `pg_dump --table=<name> --data-only` from the scratch DB.
3. `psql arena` on prod, `BEGIN; TRUNCATE <name>; \i dump.sql; COMMIT;` — gated behind a code review.

## Producing a redacted dump for staging/dev

Production data must be scrubbed before any non-prod use:

```bash
fly postgres connect --app arena-db -- pg_dump --format=custom > prod.dump
pg_restore --file=prod.sql prod.dump
python infra/scripts/redact_dump.py prod.sql > prod.redacted.sql
psql $STAGING_DATABASE_URL < prod.redacted.sql
```

`redact_dump.py` masks emails, GitHub logins, and the `text` columns of `agent_turns.user_prompt` and `command_runs.command`. Never commit a non-redacted dump.

## Smoke test after restore

Run the end-to-end Playwright smoke (`pnpm test:e2e --grep "@smoke"`). Confirm:

- Sign-in flow works (a known test account survives).
- A session can be started against Mission 01.
- A submission can be retrieved (`GET /api/v1/reports/<id>`).
- `supervision_events` row counts roughly match pre-restore numbers.

## What we test in CI

A weekly job restores the latest nightly into a scratch DB and runs the smoke. Failure pages the on-call.

## References

- [IMPLEMENTATION_PLAN.md §22](../../IMPLEMENTATION_PLAN.md) (deployment)
- [IMPLEMENTATION_PLAN.md §25](../../IMPLEMENTATION_PLAN.md) (Definition of Done — backup runbook tested)
- [incident-response.md](./incident-response.md)
