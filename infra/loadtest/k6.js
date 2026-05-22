// OpenAgentDojo — sustained-throughput load test (M8).
//
// Drives 25 RPS for 10 minutes across the four hot REST endpoints:
//   - GET  /api/v1/missions
//   - POST /api/v1/sessions                (requires a session cookie)
//   - GET  /api/v1/sessions/{id}
//   - GET  /api/v1/sessions/{id}/timeline
//
// Threshold: p95 < 800ms across the whole run.
//
// Auth: pre-seed an `ARENA_SESSION` cookie on the host running this script
// (e.g. by completing a dev magic-link flow once) and pass it via the
// `ARENA_SESSION_COOKIE` env var. If absent, the session-creating arms of
// the mix degrade to read-only requests so the script still produces a
// useful baseline.
//
// Run:
//   k6 run \
//     -e API_BASE=http://localhost:8000 \
//     -e ARENA_SESSION_COOKIE=$(cat .arena-session-cookie) \
//     infra/loadtest/k6.js
//
// CI tip: emit JSON summary with `--summary-export=loadtest-summary.json`
// and fail the job if `metrics.http_req_duration.values.p(95)` exceeds the
// threshold below — k6 already exits non-zero, but the summary file is
// useful for trend dashboards.

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";
import { SharedArray } from "k6/data";

const API_BASE = __ENV.API_BASE || "http://localhost:8000";
const SESSION_COOKIE = __ENV.ARENA_SESSION_COOKIE || "";

// Pull mission ids out of the missions list once at init so each iteration
// just walks the array instead of re-fetching the catalogue.
const MISSION_IDS = new SharedArray("mission_ids", () => {
  const seed = (__ENV.MISSION_IDS || "auth-cookie-expiration,wrong-file-edit").split(",");
  return seed.map((id) => id.trim()).filter(Boolean);
});

const sessionLatency = new Trend("arena_session_latency", true);
const sessionCreated = new Counter("arena_sessions_created");

// Mix targets: 25 RPS sustained for 10m → 15,000 total iterations.
export const options = {
  scenarios: {
    arena_mix: {
      executor: "constant-arrival-rate",
      rate: 25,
      timeUnit: "1s",
      duration: "10m",
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<800"],
    "http_req_duration{name:missions_list}": ["p(95)<400"],
    "http_req_duration{name:session_get}": ["p(95)<600"],
    "http_req_duration{name:session_timeline}": ["p(95)<800"],
    "http_req_duration{name:session_post}": ["p(95)<1500"],
  },
};

function authedHeaders() {
  const h = { "Content-Type": "application/json", Accept: "application/json" };
  if (SESSION_COOKIE) {
    h.Cookie = SESSION_COOKIE;
  }
  return h;
}

function pickMission() {
  if (MISSION_IDS.length === 0) return null;
  const idx = Math.floor(Math.random() * MISSION_IDS.length);
  return MISSION_IDS[idx];
}

let _cachedSessionId = null;

function ensureSessionId() {
  if (_cachedSessionId) return _cachedSessionId;
  if (!SESSION_COOKIE) return null;

  const missionId = pickMission();
  if (!missionId) return null;

  const res = http.post(
    `${API_BASE}/api/v1/sessions`,
    JSON.stringify({ mission_id: missionId }),
    {
      headers: authedHeaders(),
      tags: { name: "session_post" },
    }
  );
  sessionLatency.add(res.timings.duration);
  if (res.status >= 200 && res.status < 300) {
    try {
      const body = res.json();
      _cachedSessionId = body.id || (body.session && body.session.id) || null;
      if (_cachedSessionId) sessionCreated.add(1);
    } catch (_) {
      // body wasn't JSON; nothing to do.
    }
  }
  return _cachedSessionId;
}

export default function () {
  // Endpoint mix per iteration (one iteration = ~1/25 sec):
  //   60% missions list  (read-heavy, anonymous-friendly)
  //   20% session GET
  //   15% session timeline
  //    5% session POST (rate-limited at the API; this models bursty sign-ups)
  const r = Math.random();

  group("missions_list", () => {
    const res = http.get(`${API_BASE}/api/v1/missions`, {
      headers: authedHeaders(),
      tags: { name: "missions_list" },
    });
    check(res, { "missions list 2xx": (r) => r.status >= 200 && r.status < 300 });
  });

  const sid = ensureSessionId();

  if (r < 0.6 || !sid) {
    // pure missions list iteration — done.
  } else if (r < 0.8) {
    group("session_get", () => {
      const res = http.get(`${API_BASE}/api/v1/sessions/${sid}`, {
        headers: authedHeaders(),
        tags: { name: "session_get" },
      });
      check(res, { "session get 2xx": (r) => r.status >= 200 && r.status < 300 });
    });
  } else if (r < 0.95) {
    group("session_timeline", () => {
      const res = http.get(`${API_BASE}/api/v1/sessions/${sid}/timeline`, {
        headers: authedHeaders(),
        tags: { name: "session_timeline" },
      });
      check(res, { "timeline 2xx": (r) => r.status >= 200 && r.status < 300 });
    });
  } else {
    // 5% — fresh session POST (resets cached id so next iteration picks it up).
    _cachedSessionId = null;
    ensureSessionId();
  }

  // Light think time so we don't outrun the arrival rate scheduler when
  // sub-ms responses land back-to-back.
  sleep(0.05);
}
