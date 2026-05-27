#!/usr/bin/env bash
# Hidden-test runner for mission $mission_id ($language_runtime).
#
# Invoked by the grader inside the sandbox at submit time. The grader
# mounts this folder at /grader (read-only) and the workspace repo pack
# at /workspace. The runner must emit results.json in the canonical
# grader envelope: [{name, status, duration_ms, file}, ...].
#
# Implementation pattern — copy the hidden test fixture into the repo
# pack's test tree, then exec the language's native test runner. For
# Go missions, source /opt/runners/go-runner.sh which bridges
# ``go test -json`` events into the grader envelope automatically.

set -euo pipefail

GRADER_DIR=${GRADER_DIR:-"$$(cd "$$(dirname "$${BASH_SOURCE[0]}")" && pwd)"}
WORKSPACE_DIR=${WORKSPACE_DIR:-"/workspace"}
REPORT_PATH=${REPORT_PATH:-"$${GRADER_DIR}/results.json"}

if [[ ! -d "$${WORKSPACE_DIR}" ]]; then
  echo "[runner.sh] FATAL: workspace not found at $${WORKSPACE_DIR}" >&2
  exit 2
fi

# TODO: copy hidden test fixtures into the repo pack and exec the test
# runner. See missions/01-auth-cookie-expiration/hidden_tests/runner.sh
# for a Vitest example, missions/11-goroutine-leak/hidden_tests/runner.sh
# for the Go pattern.
echo "[runner.sh] TODO: implement hidden test execution for $mission_id" >&2
exit 1
