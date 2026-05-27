"""Verify the Go-test-events bridge emits the shared grader envelope.

The bridge script
(``missions/_shared/docker/runners/go-test-events-to-grader.py``) is
stdlib-only and lives under ``missions/_shared/`` (outside the API
package), so this test loads it via ``importlib.util.spec_from_file_location``
and feeds it a hand-crafted ``go test -json`` event stream.

The contract this test pins is the *envelope shape* shared with the
TS/Py runners — ``[{name, status, duration_ms, file}, ...]`` — so a
future edit that, for example, drops the per-test ``file`` field would
fail here instead of silently breaking the Go grader (P1-1).
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType


def _load_bridge() -> ModuleType:
    """Import the bridge script as a module so we can call ``translate``."""
    repo_root = Path(__file__).resolve().parents[3]
    bridge_path = (
        repo_root
        / "missions"
        / "_shared"
        / "docker"
        / "runners"
        / "go-test-events-to-grader.py"
    )
    assert bridge_path.exists(), f"bridge script missing at {bridge_path}"
    spec = importlib.util.spec_from_file_location("go_test_bridge", bridge_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_events() -> list[dict[str, object]]:
    """Hand-crafted ``go test -json`` event stream.

    Mirrors the real shape: a mix of ``run`` / ``output`` chatter, then a
    terminal ``pass`` / ``fail`` / ``skip`` per test, then a package-level
    summary. The bridge must keep only the per-test terminals.
    """
    return [
        {"Time": "2026-05-27T12:00:00Z", "Action": "run",
         "Package": "github.com/x/orders/internal/handlers",
         "Test": "TestPlaceOrder"},
        {"Action": "output", "Test": "TestPlaceOrder",
         "Output": "=== RUN   TestPlaceOrder\n"},
        {"Action": "pass",
         "Package": "github.com/x/orders/internal/handlers",
         "Test": "TestPlaceOrder", "Elapsed": 0.0123},
        {"Action": "run",
         "Package": "github.com/x/orders/internal/handlers",
         "Test": "TestCancelOrder"},
        {"Action": "fail",
         "Package": "github.com/x/orders/internal/handlers",
         "Test": "TestCancelOrder", "Elapsed": 0.5},
        {"Action": "run",
         "Package": "github.com/x/orders/internal/queue",
         "Test": "TestWorkerPool"},
        {"Action": "skip",
         "Package": "github.com/x/orders/internal/queue",
         "Test": "TestWorkerPool", "Elapsed": 0.001},
        # Package-level summary — bridge must drop it; no Test field.
        {"Action": "pass",
         "Package": "github.com/x/orders/internal/handlers",
         "Elapsed": 0.7},
        # Stray non-JSON line — bridge must skip silently.
    ]


def test_translate_envelope_shape() -> None:
    bridge = _load_bridge()
    stream = (json.dumps(e) for e in _fixture_events())
    out = bridge.translate(stream)
    assert isinstance(out, list)
    # 3 per-test outcomes (pass/fail/skip), package summary dropped.
    assert len(out) == 3

    for record in out:
        assert set(record.keys()) == {"name", "status", "duration_ms", "file"}
        assert isinstance(record["name"], str)
        assert record["status"] in {"pass", "fail", "skip"}
        assert isinstance(record["duration_ms"], int)
        assert isinstance(record["file"], str)

    # Test name uses Package.TestName for disambiguation.
    by_name = {r["name"]: r for r in out}
    assert (
        "github.com/x/orders/internal/handlers.TestPlaceOrder" in by_name
    ), "expected Package.TestName form"
    assert by_name[
        "github.com/x/orders/internal/handlers.TestPlaceOrder"
    ]["status"] == "pass"
    # Elapsed is rounded to ms (0.0123s → 12).
    assert by_name[
        "github.com/x/orders/internal/handlers.TestPlaceOrder"
    ]["duration_ms"] == 12

    fail_record = by_name["github.com/x/orders/internal/handlers.TestCancelOrder"]
    assert fail_record["status"] == "fail"
    assert fail_record["duration_ms"] == 500
    assert fail_record["file"] == "github.com/x/orders/internal/handlers"


def test_translate_ignores_malformed_lines() -> None:
    """Garbage lines (build noise, blank lines, partial JSON) must be skipped."""
    bridge = _load_bridge()
    events = [
        "",  # blank
        "not valid json",
        json.dumps({"Action": "pass", "Package": "p", "Test": "T", "Elapsed": 0.01}),
        json.dumps([1, 2, 3]),  # non-dict
        json.dumps({"Action": "output", "Output": "..."}),  # not terminal
    ]
    out = bridge.translate(iter(events))
    assert len(out) == 1
    assert out[0]["name"] == "p.T"
    assert out[0]["status"] == "pass"


def test_translate_handles_bytes() -> None:
    """Some pipelines pass bytes lines; the bridge decodes them."""
    bridge = _load_bridge()
    events = [
        json.dumps(
            {"Action": "pass", "Package": "pkg", "Test": "TByte", "Elapsed": 0.005}
        ).encode("utf-8"),
    ]
    out = bridge.translate(iter(events))
    assert out == [
        {"name": "pkg.TByte", "status": "pass", "duration_ms": 5, "file": "pkg"}
    ]


def test_main_prints_json_array() -> None:
    """``main()`` writes a single JSON array on stdout terminated with a newline."""
    bridge = _load_bridge()
    fake_stdin = io.StringIO(
        json.dumps(
            {"Action": "pass", "Package": "p", "Test": "T", "Elapsed": 0.0}
        )
        + "\n"
    )
    fake_stdout = io.StringIO()
    bridge.sys.stdin = fake_stdin
    bridge.sys.stdout = fake_stdout
    try:
        rc = bridge.main()
    finally:
        # Restore real stdio after the swap.
        import sys

        bridge.sys.stdin = sys.__stdin__
        bridge.sys.stdout = sys.__stdout__
    assert rc == 0
    payload = fake_stdout.getvalue().rstrip("\n")
    parsed = json.loads(payload)
    assert parsed == [
        {"name": "p.T", "status": "pass", "duration_ms": 0, "file": "p"}
    ]
