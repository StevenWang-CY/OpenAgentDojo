"""P1-3 — LocalSandboxDriver.spawn_lsp can drive a real LSP handshake.

These tests integrate against the *actual* pyright binary; if it isn't on
PATH the test is skipped (the design accepts this — local dev / minimal CI
shouldn't be required to bundle every LSP). The CI image is the place where
the binary IS pinned; missing it there is a configuration regression worth
catching out-of-band.

The handshake we exercise:

    initialize  (JSON-RPC id=1)  →  response with `capabilities`
    initialized (notification)
    shutdown    (JSON-RPC id=2)  →  null response
    exit        (notification)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest

from app.sandbox.local_driver import LocalSandboxDriver
from app.sandbox.lsp import LSPUnavailableError
from app.sandbox.types import SandboxHandle


def _has_pyright() -> bool:
    """True iff the platform's preferred Python LSP is on PATH."""
    return shutil.which("pyright-langserver") is not None or shutil.which("pylsp") is not None


def _frame(payload: dict) -> bytes:
    """Build a single LSP framed JSON-RPC message (Content-Length header + body)."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def _drain_until(lsp, predicate, *, timeout_s: float = 10.0) -> bytes:
    """Read from the LSP stdout until ``predicate(buffer)`` returns True or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    buf = bytearray()
    while asyncio.get_event_loop().time() < deadline:
        try:
            chunk = await asyncio.wait_for(lsp.read_stdout(), timeout=2.0)
        except TimeoutError:
            continue
        if not chunk:
            break
        buf.extend(chunk)
        if predicate(bytes(buf)):
            return bytes(buf)
    return bytes(buf)


def _make_handle(workdir: Path) -> SandboxHandle:
    return SandboxHandle(
        id=f"handle-{uuid.uuid4().hex[:8]}",
        driver="local",
        workdir=workdir,
        mission_id="lsp-test",
        session_id=uuid.uuid4(),
    )


@pytest.mark.skipif(
    not _has_pyright(),
    reason="pyright-langserver/pylsp not on PATH — install one to exercise this test",
)
@pytest.mark.asyncio
async def test_pyright_initialize_handshake_completes(tmp_path: Path) -> None:
    """End-to-end: spawn pyright via the driver, complete initialize/shutdown."""
    driver = LocalSandboxDriver()
    handle = _make_handle(tmp_path)

    # A tiny workspace file gives pyright something to root its index against.
    (tmp_path / "main.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    lsp = await driver.spawn_lsp(handle, "python")
    try:
        assert lsp.language == "python"
        assert lsp.alive is True

        # 1) initialize
        init = _frame(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "rootUri": tmp_path.as_uri(),
                    "capabilities": {},
                    "trace": "off",
                    "workspaceFolders": [
                        {"uri": tmp_path.as_uri(), "name": "lsp-test"},
                    ],
                },
            }
        )
        await lsp.write_stdin(init)

        # Pyright is verbose at start; we just need to see the response id=1.
        got = await _drain_until(
            lsp,
            lambda b: b'"id":1' in b or b'"id": 1' in b,
            timeout_s=15.0,
        )
        assert b'"id":1' in got or b'"id": 1' in got, (
            f"did not see initialize response within budget — buffer={got[:400]!r}"
        )

        # 2) initialized notification
        await lsp.write_stdin(
            _frame({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        )

        # 3) shutdown
        await lsp.write_stdin(
            _frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None})
        )
        got = await _drain_until(
            lsp,
            lambda b: b'"id":2' in b or b'"id": 2' in b,
            timeout_s=10.0,
        )
        assert b'"id":2' in got or b'"id": 2' in got, (
            f"did not see shutdown response — buffer={got[-400:]!r}"
        )

        # 4) exit notification
        await lsp.write_stdin(_frame({"jsonrpc": "2.0", "method": "exit"}))
    finally:
        await lsp.shutdown(timeout_s=2.0)
        # Once shutdown has run, alive() must be False.
        assert lsp.alive is False


@pytest.mark.asyncio
async def test_spawn_unsupported_language_raises_lsp_unavailable(tmp_path: Path) -> None:
    """spawn_lsp must surface a typed error for languages we don't ship a server for."""
    driver = LocalSandboxDriver()
    handle = _make_handle(tmp_path)
    with pytest.raises(LSPUnavailableError) as exc_info:
        await driver.spawn_lsp(handle, "rust")  # not in SUPPORTED_LANGUAGES
    assert exc_info.value.error_class == "unsupported_language"
    assert exc_info.value.language == "rust"


@pytest.mark.asyncio
async def test_spawn_missing_binary_raises_binary_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    """When NO candidate binary is on PATH, the driver raises ``binary_not_found``.

    We monkeypatch ``shutil.which`` so this test is deterministic regardless of
    what's installed on the runner.
    """
    from app.sandbox import lsp as lsp_mod

    driver = LocalSandboxDriver()
    handle = _make_handle(tmp_path)

    monkeypatch.setattr(lsp_mod.shutil, "which", lambda *_args, **_kwargs: None)

    with pytest.raises(LSPUnavailableError) as exc_info:
        await driver.spawn_lsp(handle, "python")
    assert exc_info.value.error_class == "binary_not_found"
    assert exc_info.value.language == "python"
