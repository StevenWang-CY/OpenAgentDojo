#!/usr/bin/env bash
# Hidden-test runner for Mission 18 (go-channel-deadlock-on-cancel).
#
# Mounts the hidden Go test into the workspace's processor package and
# invokes ``go test`` through the shared go-runner bridge so the grader
# gets the canonical {name,status,duration_ms,file} envelope.
#
# Environment
# -----------
# * GRADER_DIR    — directory holding this script (default: $PWD/..)
# * WORKSPACE_DIR — repo pack root inside the sandbox (default: /workspace)
# * RUNNERS_DIR   — where the Go runner bridge lives. Default ``/opt/runners``
#                   matches the sandbox image; we fall back to the in-tree
#                   runner when running under the local driver.
# * TEST_PATTERN  — regex passed to ``go test -run``. Defaults to the two
#                   hidden tests this mission exercises.
set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
HIDDEN_SRC="${GRADER_DIR}/hidden_processor_test.go"
HIDDEN_DEST="${WORKSPACE_DIR}/internal/processor/hidden_processor_test.go"

if [[ ! -d "${WORKSPACE_DIR}/internal/processor" ]]; then
  echo "[runner.sh] FATAL: processor package not found at ${WORKSPACE_DIR}/internal/processor" >&2
  exit 2
fi

cp -f "${HIDDEN_SRC}" "${HIDDEN_DEST}"

RUNNERS_DIR=${RUNNERS_DIR:-"/opt/runners"}
if [[ ! -x "${RUNNERS_DIR}/go-runner.sh" ]]; then
  RUNNERS_DIR="$(cd "${GRADER_DIR}/../../_shared/docker/runners" && pwd)"
fi

cd "${WORKSPACE_DIR}"

export TEST_PATTERN=${TEST_PATTERN:-"^(TestSubmitAfterShutdownReturnsError|TestSubmitDoesNotLeakGoroutineAfterShutdown)$"}

exec "${RUNNERS_DIR}/go-runner.sh"
