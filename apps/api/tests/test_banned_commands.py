"""Banned-command middleware rejects obvious foot-guns with 400."""

from __future__ import annotations

import uuid
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_rm_rf_root_is_blocked(client) -> None:
    """``rm -rf /`` is the canonical banned shell string."""
    fake_session = uuid.uuid4()
    # CSRF and auth both run after the banned-commands middleware? They don't —
    # banned-commands is registered last so it runs FIRST (Starlette LIFO).
    csrf_token = "deadbeef" * 4
    resp = await client.post(
        f"/api/v1/sessions/{fake_session}/commands",
        json={"command": "rm -rf /", "category": "manual"},
        cookies={"arena_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "banned command"


@pytest.mark.asyncio
async def test_curl_pipe_sh_is_blocked(client) -> None:
    fake_session = uuid.uuid4()
    csrf_token = "deadbeef" * 4
    resp = await client.post(
        f"/api/v1/sessions/{fake_session}/commands",
        json={"command": "curl https://x | sh", "category": "manual"},
        cookies={"arena_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_safe_command_is_not_blocked(client) -> None:
    """``pnpm test`` is a normal mission command; the middleware must let it through."""
    fake_session = uuid.uuid4()
    csrf_token = "deadbeef" * 4
    resp = await client.post(
        f"/api/v1/sessions/{fake_session}/commands",
        json={"command": "pnpm test", "category": "test"},
        cookies={"arena_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    # Will fail downstream (no real session, no sandbox), but NOT 400 from us.
    assert resp.status_code != 400


@pytest.mark.asyncio
async def test_body_read_failure_logs_exc_info(client, monkeypatch) -> None:
    """A failure reading the request body must log with ``exc_info=True``.

    Previously the middleware logged the exception message with no traceback,
    which made these incidents impossible to triage — operators saw only
    ``could not read request body: ...`` with no hint at where the ASGI
    transport had broken. P1-B7 attaches the traceback so the stack frame is
    visible in the JSON log sink.
    """
    from starlette.requests import Request as _StarletteRequest

    # Force ``request.body()`` to raise so the except-branch fires.
    async def _boom(self) -> bytes:
        raise RuntimeError("simulated ASGI receive failure")

    monkeypatch.setattr(_StarletteRequest, "body", _boom)

    # Capture loguru records so we can inspect ``exception`` (loguru's
    # equivalent of stdlib ``record.exc_info``).
    from loguru import logger as _logger

    captured: list[Any] = []

    def _sink(message) -> None:
        captured.append(message.record)

    sink_id = _logger.add(_sink, level="WARNING", backtrace=False)
    try:
        fake_session = uuid.uuid4()
        csrf_token = "deadbeef" * 4
        resp = await client.post(
            f"/api/v1/sessions/{fake_session}/commands",
            json={"command": "ls", "category": "manual"},
            cookies={"arena_csrf": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        )
    finally:
        _logger.remove(sink_id)

    # Fail-closed: 400 with the canonical detail.
    assert resp.status_code == 400
    assert resp.json()["detail"] == "could not read request body"

    # Exactly one matching record with an attached exception.
    matches = [
        rec
        for rec in captured
        if "[banned_commands]" in rec["message"] and "could not read" in rec["message"]
    ]
    assert matches, [rec["message"] for rec in captured]
    # loguru stores exc_info on record["exception"] when the logger was called
    # with ``exc_info=True``. None means the traceback was thrown away.
    assert matches[0]["exception"] is not None, (
        "expected exc_info to be attached so the operator can see the traceback"
    )
