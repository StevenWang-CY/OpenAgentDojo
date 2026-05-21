#!/usr/bin/env bash
set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
FRONTEND_DIR="${WORKSPACE_DIR}/frontend"
HIDDEN_DEST="${FRONTEND_DIR}/src/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.json"}

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/dashboard.hidden.test.tsx" "${HIDDEN_DEST}/dashboard.hidden.test.tsx"

cd "${FRONTEND_DIR}"
exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
