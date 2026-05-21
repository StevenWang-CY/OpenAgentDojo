#!/usr/bin/env bash
set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
BACKEND_DIR="${WORKSPACE_DIR}/backend"
HIDDEN_DEST="${BACKEND_DIR}/tests/hidden"
REPORT_PATH=${REPORT_PATH:-"${GRADER_DIR}/results.xml"}

mkdir -p "${HIDDEN_DEST}"
cp -f "${GRADER_DIR}/test_jobs_hidden.py" "${HIDDEN_DEST}/test_jobs_hidden.py"

cd "${BACKEND_DIR}"
exec uv run --frozen pytest tests/hidden -q --junitxml="${REPORT_PATH}"
