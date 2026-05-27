#!/usr/bin/env bash
# Hidden-test runner for Mission 13 (error-shadowed-by-wrap).

set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
HIDDEN_SRC="${GRADER_DIR}/hidden_handlers_test.go"
HIDDEN_DEST="${WORKSPACE_DIR}/internal/handlers/hidden_handlers_test.go"

if [[ ! -d "${WORKSPACE_DIR}/internal/handlers" ]]; then
  echo "[runner.sh] FATAL: handlers package not found at ${WORKSPACE_DIR}/internal/handlers" >&2
  exit 2
fi

cp -f "${HIDDEN_SRC}" "${HIDDEN_DEST}"

RUNNERS_DIR=${RUNNERS_DIR:-"/opt/runners"}
if [[ ! -x "${RUNNERS_DIR}/go-runner.sh" ]]; then
  RUNNERS_DIR="$(cd "${GRADER_DIR}/../../_shared/docker/runners" && pwd)"
fi

cd "${WORKSPACE_DIR}"

export TEST_PATTERN=${TEST_PATTERN:-"^(TestGetMissingOrderReturns404|TestShipMissingOrderReturns404|TestLookupOrderPreservesSentinel)$"}

exec "${RUNNERS_DIR}/go-runner.sh"
