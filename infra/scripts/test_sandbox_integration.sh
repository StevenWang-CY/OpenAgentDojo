#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_sandbox_integration.sh — M2 exit-gate check.
#
# Provisions a sandbox via the API for Mission 01, runs `pnpm test:unit`
# inside it, and asserts the command exited 0. This is the milestone gate
# proving the sandbox driver + WS bridge actually work end-to-end.
#
# Prereqs:
#   - The compose stack is already running (`pnpm compose:up`).
#   - Mission 01 is seeded (`infra/scripts/seed_dev.sh`).
#   - A test user exists or auth is disabled in dev (see app.config).
#
# Env knobs:
#   API_BASE_URL    default http://localhost:8000
#   MISSION_ID      default auth-cookie-expiration
#   AUTH_TOKEN      optional bearer token if /sessions requires auth in dev
#
# Usage:
#   infra/scripts/test_sandbox_integration.sh
# ---------------------------------------------------------------------------
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
MISSION_ID="${MISSION_ID:-auth-cookie-expiration}"
AUTH_TOKEN="${AUTH_TOKEN:-}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[sandbox-it] missing required command: $1" >&2
    exit 127
  fi
}
need curl
need jq

auth_header=()
if [[ -n "${AUTH_TOKEN}" ]]; then
  auth_header=(-H "Authorization: Bearer ${AUTH_TOKEN}")
fi

echo "[sandbox-it] api: ${API_BASE_URL}"
echo "[sandbox-it] mission: ${MISSION_ID}"

echo "[sandbox-it] sanity: GET /healthz"
curl -fsS "${API_BASE_URL}/healthz" >/dev/null

echo "[sandbox-it] creating session..."
create_payload=$(jq -nc --arg m "${MISSION_ID}" '{mission_id:$m}')
session=$(curl -fsS -X POST "${API_BASE_URL}/api/v1/sessions" \
  -H "Content-Type: application/json" \
  "${auth_header[@]}" \
  -d "${create_payload}")
session_id=$(echo "${session}" | jq -r '.id')
if [[ -z "${session_id}" || "${session_id}" == "null" ]]; then
  echo "[sandbox-it] failed to create session: ${session}" >&2
  exit 1
fi
echo "[sandbox-it] session_id=${session_id}"

echo "[sandbox-it] waiting for sandbox to become active..."
for _ in $(seq 1 60); do
  status=$(curl -fsS "${API_BASE_URL}/api/v1/sessions/${session_id}" "${auth_header[@]}" | jq -r '.status')
  if [[ "${status}" == "active" ]]; then
    echo "[sandbox-it] sandbox active"
    break
  fi
  if [[ "${status}" == "error" || "${status}" == "abandoned" ]]; then
    echo "[sandbox-it] session ended unexpectedly: ${status}" >&2
    exit 1
  fi
  sleep 2
done

echo "[sandbox-it] running pnpm test:unit inside sandbox..."
run_payload=$(jq -nc '{command:"pnpm test:unit", category:"test"}')
run_result=$(curl -fsS -X POST "${API_BASE_URL}/api/v1/sessions/${session_id}/commands" \
  -H "Content-Type: application/json" \
  "${auth_header[@]}" \
  -d "${run_payload}")

exit_code=$(echo "${run_result}" | jq -r '.exit_code')
echo "[sandbox-it] exit_code=${exit_code}"

if [[ "${exit_code}" != "0" ]]; then
  echo "[sandbox-it] FAIL — sandbox returned non-zero" >&2
  echo "${run_result}" | jq .
  exit 1
fi

echo "[sandbox-it] PASS — M2 exit-gate satisfied"
