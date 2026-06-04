#!/usr/bin/env bash
# Hidden-test runner for Mission 14 (react-shop-state-desync).
#
# Invoked by the grader inside the sandbox at submit time. The grader has
# mounted this folder at /grader (read-only) and the workspace repo pack
# at /workspace.
#
# We copy the hidden test file into the frontend's `src/tests/hidden/`
# directory (which the repo pack ships empty), then run vitest against it
# with the JSON reporter so the grader can extract per-test results.

set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
FRONTEND_DIR="${WORKSPACE_DIR}/frontend"
HIDDEN_DEST="${FRONTEND_DIR}/src/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.json"}

if [[ ! -d "${FRONTEND_DIR}" ]]; then
  echo "[runner.sh] FATAL: frontend dir not found at ${FRONTEND_DIR}" >&2
  exit 2
fi

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/useCart.hidden.test.tsx" "${HIDDEN_DEST}/useCart.hidden.test.tsx"

cd "${FRONTEND_DIR}"

# Run via the repo pack's installed vitest so we honor whatever Vitest /
# jsdom config + runtime version the pack ships with.
exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
