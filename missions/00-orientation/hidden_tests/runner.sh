#!/usr/bin/env bash
# Hidden-test runner for Mission 00 (orientation).
#
# The orientation mission grades by *completion*, not by score (see the
# grading runner's tutorial short-circuit in app/grading/runner.py).
# This runner still exists so the visible/hidden split is a real thing
# the supervisor experiences during the tutorial — the result of running
# this script is what the FE shows in the report's "hidden test results"
# strip.
#
# The grader mounts this folder at /grader (read-only) and the workspace
# repo pack at /workspace.

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
cp -f "${GRADER_DIR}/welcome.hidden.test.ts" "${HIDDEN_DEST}/welcome.hidden.test.ts"

cd "${BACKEND_DIR}"

exec pnpm vitest run src/tests/hidden \
  --reporter=default \
  --reporter=json \
  --outputFile.json="${REPORT_PATH}"
