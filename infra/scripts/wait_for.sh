#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# wait_for.sh — block until host:port accepts TCP, or timeout.
#
# Usage:
#   wait_for.sh <host> <port> [timeout_seconds]
#
# Example:
#   wait_for.sh localhost 5432 60
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <host> <port> [timeout_seconds]" >&2
  exit 64
fi

HOST="$1"
PORT="$2"
TIMEOUT="${3:-60}"

start_ts=$(date +%s)

while true; do
  if (echo > "/dev/tcp/${HOST}/${PORT}") >/dev/null 2>&1; then
    echo "[wait_for] ${HOST}:${PORT} is open"
    exit 0
  fi

  now=$(date +%s)
  elapsed=$(( now - start_ts ))
  if (( elapsed >= TIMEOUT )); then
    echo "[wait_for] timed out after ${TIMEOUT}s waiting for ${HOST}:${PORT}" >&2
    exit 1
  fi

  sleep 1
done
