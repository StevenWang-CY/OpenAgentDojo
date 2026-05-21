#!/usr/bin/env bash
# Hidden-test runner for Mission 01.
#
# Invoked by the grader inside the sandbox at submit time. The grader has
# mounted this folder at /grader (read-only) and the workspace repo pack
# at /workspace.
#
# We copy the hidden test file into the backend's `src/tests/hidden/`
# directory (which the repo pack ships empty), then run `pnpm test:hidden`
# from the backend package. Vitest's machine-readable JSON reporter is
# captured so the grader can extract per-test results.

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
cp -f "${GRADER_DIR}/auth.hidden.test.ts" "${HIDDEN_DEST}/auth.hidden.test.ts"

cd "${BACKEND_DIR}"

# Run via the existing test:hidden script so we honor whatever Vitest
# config / runtime version the repo pack ships with.
exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
