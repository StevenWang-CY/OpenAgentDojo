# OpenAgentDojo load tests

`k6.js` drives the four hot read paths plus a small session-creation arm at a
constant 25 RPS for 10 minutes and enforces `http_req_duration p(95) < 800ms`.

## Prerequisites

- [k6](https://k6.io/docs/get-started/installation/) (`brew install k6`)
- The compose stack running locally (`docker compose -f infra/compose/docker-compose.yml up -d`)
- A valid `arena_session` cookie. Easiest path:

  ```bash
  # Complete the magic-link flow once in the browser, then copy the cookie:
  echo 'arena_session=<value-from-browser>' > .arena-session-cookie
  ```

  Or hit the dev-only login helper if your fixtures expose one.

## Run

```bash
k6 run \
  -e API_BASE=http://localhost:8000 \
  -e ARENA_SESSION_COOKIE="$(cat .arena-session-cookie)" \
  -e MISSION_IDS="auth-cookie-expiration,wrong-file-edit,duplicate-submit-regression" \
  infra/loadtest/k6.js
```

Without a session cookie the script still runs the read-only mix — useful
for a quick sanity check against staging — but the session POST/GET arms are
skipped and the test only validates the mission-catalogue path.

## CI

In CI use the `--summary-export` flag to capture metrics for trend tracking:

```bash
k6 run --summary-export=loadtest-summary.json infra/loadtest/k6.js
```

k6 already exits non-zero when a threshold trips, so the job fails cleanly
if `p(95)` regresses beyond 800ms.

## Interpreting failures

| Symptom                                  | Likely cause                                   |
| ---------------------------------------- | ---------------------------------------------- |
| `http_req_duration p(95)` over budget    | DB pool saturation / sandbox pool starvation   |
| `session_post p(95)` over budget         | RQ worker backlog (check `provision` queue)    |
| `http_req_failed > 2%`                   | Rate-limit middleware tripping on a hot tenant |
| `arena_sessions_created == 0`            | Cookie expired or `MISSION_IDS` empty          |

Resolve in that order: tune the bottleneck before bumping the threshold.
