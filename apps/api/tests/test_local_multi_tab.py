"""Local sandbox driver supports multiple terminal tabs per handle.

We open three PTYs against a single handle and confirm that ``destroy``
closes every one of them — not just the last attached.
"""

from __future__ import annotations

import os
import shutil
import uuid

import pytest

GIT_PRESENT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not GIT_PRESENT, reason="git not installed on this host")


class _FakeMission:
    id = "fake-mission"

    class repo:  # noqa: N801
        pack = "__no_such_pack__"
        language_runtime = "node20"


def _pty_alive(fd: int) -> bool:
    """An open PTY fd accepts a zero-length write; closed ones raise EBADF."""
    try:
        os.write(fd, b"")
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_attach_shell_three_tabs_then_destroy_closes_all(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))

    from app.config import get_settings
    from app.sandbox.local_driver import LocalSandboxDriver

    get_settings.cache_clear()
    driver = LocalSandboxDriver()
    handle = await driver.provision(_FakeMission(), uuid.uuid4())

    tabs: list[tuple[int, object, str]] = []
    for _ in range(3):
        fd, proc, ptyid = await driver.attach_shell(handle)
        tabs.append((fd, proc, ptyid))

    # All three PTYs must be registered under unique ids, and every fd alive.
    fds = {t[0] for t in tabs}
    ptyids = {t[2] for t in tabs}
    assert len(fds) == 3, "expected three distinct pty fds"
    assert len(ptyids) == 3, "expected three distinct ptyids"
    for fd, _proc, _ptyid in tabs:
        assert _pty_alive(fd), f"pty fd {fd} should be open after attach"

    # The handle's ptys map mirrors what we got back.
    pty_map = handle.driver_state.get("ptys")
    assert isinstance(pty_map, dict)
    assert set(pty_map.keys()) == ptyids

    # Closing one tab should leave the others alone.
    first_id = tabs[0][2]
    driver.close_pty(handle, first_id)
    assert first_id not in handle.driver_state["ptys"]
    assert _pty_alive(tabs[1][0])
    assert _pty_alive(tabs[2][0])

    # Destroying the handle must close every remaining PTY.
    await driver.destroy(handle)
    for fd, _proc, _ptyid in tabs[1:]:
        assert not _pty_alive(fd), f"pty fd {fd} should be closed after destroy"
    assert not handle.driver_state.get("ptys")
