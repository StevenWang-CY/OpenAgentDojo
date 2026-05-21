#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# dev.sh — one-shot "bring the platform up locally" command.
#
# Sequence:
#   1. Ensure .env exists (copy from .env.example on first run).
#   2. `docker compose up -d` the full stack.
#   3. Wait for postgres + api + web healthchecks to go green.
#   4. Run seed_dev.sh to migrate + load missions.
#   5. Print the URLs a developer needs.
#
# Usage:
#   infra/scripts/dev.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/infra/compose/docker-compose.yml"

cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Minimum tooling: Docker Compose v2. The compose file uses v2 features
# (named profiles, depends_on conditions), so an older v1 plugin will
# silently misbehave. Check up front and bail with a clear message.
# ---------------------------------------------------------------------------
if ! docker compose version >/dev/null 2>&1; then
  echo "[dev] ERROR: 'docker compose' (v2) not found." >&2
  echo "[dev] Install Docker Desktop >= 4.0 or the docker-compose-plugin package." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "[dev] no .env found; copying from .env.example"
  cp .env.example .env
fi

echo "[dev] bringing up compose stack ($(docker compose version --short 2>/dev/null || echo 'v2'))..."
docker compose -f "${COMPOSE_FILE}" up -d

echo "[dev] waiting for postgres..."
"${SCRIPT_DIR}/wait_for.sh" localhost 5432 90

echo "[dev] waiting for api /healthz..."
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
    echo "[dev] api healthy"
    break
  fi
  sleep 2
done

echo "[dev] running migrations + mission loader..."
"${SCRIPT_DIR}/seed_dev.sh" || {
  echo "[dev] seed_dev failed — you can re-run it manually with: infra/scripts/seed_dev.sh" >&2
}

cat <<EOF

[dev] stack is up:
  web   : http://localhost:3000
  api   : http://localhost:8000     (docs: /docs, health: /healthz)
  minio : http://localhost:9001     (user: arena-minio  / pass: arena-minio-secret)
  mail  : http://localhost:8025
  pg    : postgres://arena:arena@localhost:5432/arena
  redis : redis://localhost:6379/0

[dev] tail logs: pnpm compose:logs
[dev] tear down: pnpm compose:down

EOF
