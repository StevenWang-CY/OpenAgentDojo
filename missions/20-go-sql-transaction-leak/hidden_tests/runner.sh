#!/usr/bin/env bash
# Hidden-test runner for Mission 20 (go-sql-transaction-leak).
#
# Mounts the hidden Go test into the workspace's store package and invokes
# ``go test`` through the shared go-runner bridge so the grader gets the
# canonical {name,status,duration_ms,file} envelope.
#
# Environment
# -----------
# * GRADER_DIR    — directory holding this script (default: $PWD/..)
# * WORKSPACE_DIR — repo pack root inside the sandbox (default: /workspace)
# * RUNNERS_DIR   — where the Go runner bridge lives. Default ``/opt/runners``
#                   matches the sandbox image; we fall back to the in-tree
#                   runner when running under the local driver.
# * TEST_PATTERN  — regex passed to ``go test -run``. Defaults to the three
#                   hidden tests this mission exercises.
set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
HIDDEN_SRC="${GRADER_DIR}/hidden_bulk_test.go"
HIDDEN_DEST="${WORKSPACE_DIR}/internal/store/hidden_bulk_test.go"

if [[ ! -d "${WORKSPACE_DIR}/internal/store" ]]; then
  echo "[runner.sh] FATAL: store package not found at ${WORKSPACE_DIR}/internal/store" >&2
  exit 2
fi

cp -f "${HIDDEN_SRC}" "${HIDDEN_DEST}"

# Locate the runner bridge. The grader image places it at /opt/runners,
# but the local driver runs out of the repo tree.
RUNNERS_DIR=${RUNNERS_DIR:-"/opt/runners"}
if [[ ! -x "${RUNNERS_DIR}/go-runner.sh" ]]; then
  RUNNERS_DIR="$(cd "${GRADER_DIR}/../../_shared/docker/runners" && pwd)"
fi

cd "${WORKSPACE_DIR}"

export TEST_PATTERN=${TEST_PATTERN:-"^(TestBulkUpdateReleasesConnOnUnknownID|TestBulkUpdateStoreUsableAfterFailedBatch|TestBulkUpdateIsAtomicOnUnknownID)$"}

exec "${RUNNERS_DIR}/go-runner.sh"
