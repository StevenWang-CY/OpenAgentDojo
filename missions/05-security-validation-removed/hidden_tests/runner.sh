#!/usr/bin/env bash
set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
BACKEND_DIR="${WORKSPACE_DIR}/backend"
HIDDEN_DEST="${BACKEND_DIR}/src/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.json"}

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/settings.hidden.test.ts" "${HIDDEN_DEST}/settings.hidden.test.ts"

cd "${BACKEND_DIR}"
exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
