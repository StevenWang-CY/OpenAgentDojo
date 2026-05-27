#!/usr/bin/env bash
# Go test runner bridge for the OpenAgentDojo grader (P1-1).
#
# Runs ``go test -run "$TEST_PATTERN" -json ./...`` and pipes the
# event-stream output into the sibling Python script that translates
# Go's ``-json`` events into the existing grader envelope shape
# (``[{name, status, duration_ms, file}, ...]``) shared with the TS/Py
# runners.
#
# Environment
# -----------
# * ``TEST_PATTERN`` — required. Matches Go's ``-run`` flag (regex over
#   ``Package.TestName``). The mission ``hidden_tests.command`` field
#   sets this when the grader invokes the runner inside the sandbox.
#
# The script intentionally uses ``-json`` (not ``-v``) so the bridge
# never has to parse free-form Go test output; every event lands as
# one JSON object per line.
set -euo pipefail

: "${TEST_PATTERN:?TEST_PATTERN must be set; the grader sets this from hidden_tests.command}"

BRIDGE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/go-test-events-to-grader.py"

exec go test -run "${TEST_PATTERN}" -json ./... | python3 "${BRIDGE_SCRIPT}"
