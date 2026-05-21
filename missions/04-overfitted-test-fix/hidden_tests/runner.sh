#!/usr/bin/env bash
# Hidden-test runner for Mission 04.
#
# Copies the hidden pytest module into backend/tests/hidden/ and runs it
# via the project's existing test:hidden script. Emits a JUnit XML
# report for the grader to consume.

set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
BACKEND_DIR="${WORKSPACE_DIR}/backend"
HIDDEN_DEST="${BACKEND_DIR}/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.xml"}

if [[ ! -d "${BACKEND_DIR}" ]]; then
  echo "[runner.sh] FATAL: backend dir not found at ${BACKEND_DIR}" >&2
  exit 2
fi

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/test_calc_hidden.py" "${HIDDEN_DEST}/test_calc_hidden.py"

cd "${BACKEND_DIR}"

exec uv run --frozen pytest tests/hidden \
  -q \
  --junitxml="${REPORT_PATH}"
