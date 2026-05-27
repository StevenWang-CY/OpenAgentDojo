#!/usr/bin/env python3
"""Translate Go's ``go test -json`` event stream into the grader envelope.

Run inside the Go-pack sandbox by ``go-runner.sh``. Reads ``-json``
events one per line from stdin and emits a single JSON array on stdout
matching the shared runner envelope used by the TS/Py runners::

    [
      {"name": "<package>.<TestName>",
       "status": "pass" | "fail" | "skip",
       "duration_ms": <int>,
       "file": "<package>"}
    , ...]

Design notes
------------
* Self-contained — depends only on the Python standard library. The
  bridge ships into the Go sandbox image where extra deps would only
  slow down cold-start.
* Test name uses the canonical ``Package.TestName`` form so the grader
  can disambiguate two tests with the same name across packages
  (Go allows this).
* Status maps from the Go event's ``Action`` field at the per-test
  granularity: ``pass`` / ``fail`` / ``skip``. Package-level events
  (without a ``Test`` field) are ignored — they describe the build
  result, not a single test outcome.
* Duration is taken from the ``Elapsed`` field on the terminal event
  (``pass`` / ``fail`` / ``skip``) and rounded to milliseconds. Go
  emits ``Elapsed`` in fractional seconds.
* ``file`` is the package path. Go's ``-json`` does not surface a per-
  test source file; the package path is the closest stable proxy and
  the grader does not actually require an exact filename for Go
  missions — the closed-vocabulary failure-mode tag covers the
  pedagogical link.
* The bridge collects malformed lines (anything that doesn't parse as
  JSON) and skips them; ``go test`` can interleave a trailing build
  error line in some configurations. The grader treats an empty
  envelope as zero passing tests, which is the right failure mode for
  the user.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Go ``Action`` values we treat as terminal per-test outcomes. Anything
# else (``run``, ``output``, ``cont``, ``pause``, ``bench``) is metadata.
_TERMINAL_ACTIONS: frozenset[str] = frozenset({"pass", "fail", "skip"})


def _parse_event(line: str) -> dict[str, Any] | None:
    """Parse one ``-json`` event line; return ``None`` on garbage."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _record_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Project a terminal per-test event into the grader envelope shape.

    Returns ``None`` for events that don't describe a per-test outcome
    (package-level results, ``run``/``output`` chatter, etc.).
    """
    action = event.get("Action")
    test = event.get("Test")
    package = event.get("Package", "")
    if action not in _TERMINAL_ACTIONS or not isinstance(test, str) or not test:
        return None

    name = f"{package}.{test}" if package else test
    elapsed = event.get("Elapsed", 0.0)
    try:
        duration_ms = int(round(float(elapsed) * 1000))
    except (TypeError, ValueError):
        duration_ms = 0

    return {
        "name": name,
        "status": action,
        "duration_ms": duration_ms,
        "file": package,
    }


def translate(stream: object) -> list[dict[str, Any]]:
    """Read line-oriented ``-json`` events from ``stream`` and return records.

    Records keep insertion order (the order Go emitted the terminal
    events) so a downstream consumer can present pass/fail in test-run
    order without re-sorting.
    """
    records: list[dict[str, Any]] = []
    # ``stream`` is whatever the caller hands us; we only need iteration
    # support. Using a permissive type so the function is trivially
    # testable with a list-of-strings fixture.
    for raw_line in stream:  # type: ignore[attr-defined]
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        event = _parse_event(raw_line)
        if event is None:
            continue
        record = _record_from_event(event)
        if record is not None:
            records.append(record)
    return records


def main() -> int:
    records = translate(sys.stdin)
    json.dump(records, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
