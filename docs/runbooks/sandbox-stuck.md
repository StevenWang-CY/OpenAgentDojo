# Runbook: Sandbox Stuck or Unresponsive

A "stuck sandbox" is any sandbox that doesn't reach `active` within 25 s of provisioning, freezes mid-session, or stays alive past the 30-minute hard cap. This runbook covers triage on the worker host and manual cleanup.

## Symptoms

- User reports "the workspace is loading forever" or "my terminal stopped responding."
- The session row is stuck in `provisioning` or `active` long past its expected duration.
- `sessions_active` Prometheus gauge stays elevated after expected drop-off.
- Worker host CPU pinned by a runaway container.

## Triage

### 1. Identify the session

```bash
fly ssh console --app arena-workers
# inside the worker:
psql "$DATABASE_URL" -c \
  "SELECT id, user_id, mission_id, status, sandbox_id, started_at, completed_at
   FROM sessions
   WHERE status IN ('provisioning','active')
   AND started_at < now() - interval '30 minutes'
   ORDER BY started_at;"
```

Note the `sandbox_id` (Docker container id) and the `id` (session UUID).

### 2. Inspect the container

```bash
docker ps --filter "id=<sandbox_id>"
docker stats --no-stream <sandbox_id>
docker top <sandbox_id>
docker logs --tail 200 <sandbox_id>
```

Look for:

- A test process eating CPU but not exiting → likely an infinite loop in the user's edit.
- `permission denied` in the logs → image baking issue; capture and file.
- No logs at all → the container started but the PTY bridge died; pool reaper should pick this up automatically.

### 3. Check the pool reaper

The pool reaper (`apps/api/app/sandbox/pool.py`) runs every 60 s and kills containers that:

- Exceed the 30-minute hard cap.
- Have no associated session in `active` status.
- Have not emitted any event in the last 10 minutes (idle session timeout).

If the reaper is healthy, `pool_reaper_runs_total` should increment every minute. If not:

```bash
# on the worker:
ps -ef | grep pool
journalctl -u arena-worker -n 200 | grep -i reaper
```

If the reaper process is missing, restart the worker:

```bash
fly machine restart <worker-machine-id> --app arena-workers
```

### 4. Force-kill from the pool API

The cleanest manual cleanup is via the pool's internal endpoint (auth required, ops-only):

```bash
curl -X POST -H "Authorization: Bearer $OPS_TOKEN" \
  https://arena-api.internal/internal/sandbox/<sandbox_id>/destroy
```

This:

1. Marks the session `error` with `reason=manual_cleanup`.
2. Emits a `session.abandoned` event.
3. Calls `docker stop --time=5 <sandbox_id>` then `docker rm -f <sandbox_id>`.
4. Persists any partial artifacts to S3.

### 5. Last-resort docker cleanup

If the pool API is itself stuck:

```bash
# Be careful — only run on the worker host, never on the api host.
docker stop --time=5 <sandbox_id> || docker kill <sandbox_id>
docker rm -f <sandbox_id>
```

Then update the session row by hand:

```sql
UPDATE sessions
SET status = 'error', completed_at = now()
WHERE id = '<session_uuid>';
```

And emit a manual `session.abandoned` event:

```sql
INSERT INTO supervision_events (session_id, event_type, payload)
VALUES ('<session_uuid>', 'session.abandoned',
        '{"reason": "sandbox_error"}'::jsonb);
```

## Wider outage (many sandboxes stuck)

If multiple sessions are stuck simultaneously, that's a worker-host or Docker-daemon issue, not a per-session bug.

1. Check Docker daemon health:
   ```bash
   systemctl status docker
   docker info | grep -E "Storage Driver|Cgroup Driver|Live Restore Enabled"
   ```
2. Disk pressure is a common culprit:
   ```bash
   df -h /var/lib/docker
   docker system df
   ```
   If `Reclaimable` is large, run `docker system prune -af` (operational, not destructive of running containers).
3. If the daemon itself is wedged, drain the worker (set its capacity to 0 in Fly), let inflight requests fail to retry on a sibling worker, then `systemctl restart docker` and re-enable capacity.
4. Open an incident per [incident-response.md](./incident-response.md). The "many sandboxes stuck" pattern is a P1.

## Post-incident

- Add a regression test if a code path caused the stuck state (e.g., a missing timeout in `Sandbox.run`).
- If the pool reaper missed the stuck container, file a ticket against the reaper's heuristics.
- Update this runbook with the new failure mode if it's not already covered.

## References

- [ADR 0005: Sandbox isolation](../adr/0005-sandbox-isolation.md)
- [IMPLEMENTATION_PLAN.md §9](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §21](../../IMPLEMENTATION_PLAN.md)
