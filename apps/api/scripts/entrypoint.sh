#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Container entrypoint for apps/api.
#
# Idempotently brings the database to a usable state before handing off to
# the CMD. Steps:
#
#   1. Run `alembic upgrade head` so the schema matches the codebase.
#   2. Run the mission loader so /missions on disk is the source of truth.
#   3. exec "$@" so signals + PID 1 land on the real server process.
#
# Set ARENA_SKIP_MIGRATE=1 to bypass step 1 (useful when iterating on the
# image without a database side-car). Set ARENA_SKIP_SEED=1 to bypass step 2.
# Both are no-ops in CI tests because pytest runs migrations itself.
# ---------------------------------------------------------------------------
set -euo pipefail

cd /app

if [[ -z "${ARENA_SKIP_MIGRATE:-}" ]]; then
  echo "[entrypoint] running alembic upgrade head"
  # Retry briefly so transient "postgres still booting" doesn't kill the boot.
  for attempt in 1 2 3 4 5; do
    if alembic upgrade head; then
      break
    fi
    echo "[entrypoint] alembic attempt ${attempt} failed; retrying in 3s"
    sleep 3
    if [[ "${attempt}" == "5" ]]; then
      echo "[entrypoint] alembic upgrade head failed after 5 attempts" >&2
      exit 1
    fi
  done
fi

if [[ -z "${ARENA_SKIP_SEED:-}" ]]; then
  echo "[entrypoint] loading mission catalog from ${MISSIONS_ROOT:-/missions}"
  # The loader is best-effort — a content bug must not prevent the API from
  # starting (a missing mission row still surfaces a clean 404 to the user).
  if ! python -m app.missions.loader --seed; then
    echo "[entrypoint] mission loader failed; continuing with whatever is in the DB" >&2
  fi
fi

exec "$@"
