#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# seed_dev.sh — bring a fresh dev DB to a usable state.
#
# Idempotent: safe to re-run. Operations:
#   1. Wait for Postgres to accept connections.
#   2. Run `alembic upgrade head` against apps/api.
#   3. Run the mission loader to upsert the catalog from /missions.
#
# Honours $DATABASE_URL / $SYNC_DATABASE_URL from the environment. By default
# uses the values from the root .env file (or .env.example as a fallback).
#
# Usage:
#   infra/scripts/seed_dev.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
API_DIR="${REPO_ROOT}/apps/api"

# Load env if present.
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +o allexport
fi

DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "[seed_dev] waiting for postgres at ${DB_HOST}:${DB_PORT}..."
"${SCRIPT_DIR}/wait_for.sh" "${DB_HOST}" "${DB_PORT}" 60

cd "${API_DIR}"

# Prefer uv if available, otherwise fall back to the active venv.
if command -v uv >/dev/null 2>&1; then
  RUNNER=(uv run)
else
  RUNNER=()
  echo "[seed_dev] uv not found on PATH; falling back to bare python" >&2
fi

echo "[seed_dev] running alembic upgrade head..."
"${RUNNER[@]}" alembic upgrade head

echo "[seed_dev] running mission loader (idempotent upsert)..."
"${RUNNER[@]}" python -m app.missions.loader

# Demo users are only useful in dev/staging. The seed script itself refuses
# to run when ARENA_ENV=production, but skip the call entirely in prod to
# keep this script free of unnecessary noise.
if [[ "${ARENA_ENV:-development}" != "production" ]]; then
  echo "[seed_dev] seeding demo users (alice/bob/carol)..."
  "${RUNNER[@]}" python -m app.scripts.seed_demo_users || {
    echo "[seed_dev] demo-user seed failed; continuing (non-fatal in dev)." >&2
  }
fi

echo "[seed_dev] done."
