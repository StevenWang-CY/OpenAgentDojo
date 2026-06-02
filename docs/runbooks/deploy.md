# Runbook: Deploy & Schema Migrations

How code reaches production and how database migrations are sequenced
around it. Read alongside [`rotate-secrets.md`](./rotate-secrets.md)
(secret rollover) and [`db-restore.md`](./db-restore.md) (recovery if
a migration goes wrong).

## What this runbook covers

- The minimum pre-flight before rolling out new application code.
- Alembic migration ordering, including the post-mortem fix for the
  dual-heads incident.
- What to do when a future migration set produces multiple heads.

## What this runbook does **not** cover

- Image build / CI green-bar policy (lives in the GitHub Actions
  workflow itself).
- Edge / DNS / Cloudflare flips.
- Data-layer recovery from a bad migration — see
  [`db-restore.md`](./db-restore.md).

## Schema migrations on deploy

**Always run `alembic upgrade head` as part of the deploy pipeline
*before* rolling out new application code.** New application code is
permitted to assume the schema is at HEAD; old application code keeps
working against HEAD as long as migrations are additive (the
column/table they read still exists). Rolling code first and migrations
second flips that invariant on its head and turns a routine deploy
into an incident.

Concretely, the deploy job should:

1. `alembic upgrade head` against the production database.
2. Roll the API and worker containers to the new image.
3. Roll the web image.
4. Smoke `GET /healthz` + a real `GET /missions` from outside the VPC.

If step 1 fails, abort. Step 2 has not happened yet, so the running
application still matches the on-disk schema.

### The dual-heads incident — and the fix

After P0-7 (GitHub OAuth, migration `0021_github_oauth.py`) and P0-8
(proctored sessions, migration `0022_session_mode.py`) landed on
parallel branches, both declared
`down_revision = "0020_session_reset_event"`. Alembic therefore saw
two heads (`0021_github_oauth`, `0022_session_mode`) and refused to
`upgrade head` because it could not pick one.

The fix is **`0023_merge_oauth_session_mode.py`** — an empty merge
migration whose `down_revision` is the tuple of the two parents. The
upgrade and downgrade bodies are intentionally empty; the migration's
only job is to declare a single new head so future migrations have an
unambiguous parent.

For a Postgres production database currently at `0020`, the chain
applies cleanly:

```
0020_session_reset_event
    → 0021_github_oauth        (either order; both have 0020 as parent)
    → 0022_session_mode
        → 0023_merge_oauth_session_mode  (merge, no schema work)
            → 0024_*           (future migrations parent off 0023)
```

Dev databases that already advanced down one of the two pre-merge
branches keep working: `alembic upgrade head` walks the missing arm
of the fork, then applies the merge.

### What to do if multiple heads appear again

If a future `alembic heads` lists more than one revision, the
resolution is:

```bash
alembic merge -m "merge <topic1> + <topic2>" <head1> <head2>
```

**Do not re-parent one of the existing migrations.** Re-parenting
breaks every dev DB that already applied the now-orphaned migration
under its original parent — Alembic's `version` row no longer matches
the on-disk graph and the next `upgrade head` errors with
`Can't locate revision`. The merge migration is harmless: it preserves
both ancestor paths and just declares a new single head.

After landing the merge migration locally, re-run `alembic heads` and
confirm a single revision is reported before opening the PR.

## Pre-flight checklist

Run before every production deploy. CI gates the first three; the
remaining two are operator habits.

- [ ] Latest `main` is green (lint, unit, integration, e2e).
- [ ] `apps/api/scripts/check_env_examples.py` exits `0` — env-example
      drift between root / api / compose would catch a missing key.
- [ ] `alembic heads` reports exactly one revision (see above).
- [ ] Required new secrets have been written to Fly secrets (see
      [`rotate-secrets.md`](./rotate-secrets.md)).
- [ ] If the deploy includes a new env knob, the cutover plan covers
      the case where the value is unset (the config validator should
      refuse to boot rather than silently picking a dev default).

## Related

- [`rotate-secrets.md`](./rotate-secrets.md) — secret rollover.
- [`db-restore.md`](./db-restore.md) — recovery if a migration leaves
  the database in a bad state.
- [`incident-response.md`](./incident-response.md) — what to do when a
  deploy goes sideways.
- [`apps/api/alembic/versions/0023_merge_oauth_session_mode.py`](../../apps/api/alembic/versions/0023_merge_oauth_session_mode.py)
  — the merge migration that resolved the dual-heads incident.
