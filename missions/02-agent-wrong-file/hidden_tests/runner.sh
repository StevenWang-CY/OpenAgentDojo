#!/usr/bin/env bash
# Hidden-test runner for Mission 02.
#
# Copies the hidden vitest file into the backend's `src/tests/hidden/` and
# invokes vitest with the JSON reporter for the grader to consume.

set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
BACKEND_DIR="${WORKSPACE_DIR}/backend"
HIDDEN_DEST="${BACKEND_DIR}/src/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.json"}

if [[ ! -d "${BACKEND_DIR}" ]]; then
  echo "[runner.sh] FATAL: backend dir not found at ${BACKEND_DIR}" >&2
  exit 2
fi

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/users.hidden.test.ts" "${HIDDEN_DEST}/users.hidden.test.ts"

cd "${BACKEND_DIR}"

exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
