"""Local sandbox driver — full lifecycle test (no Docker required)."""

from __future__ import annotations

import shutil
import uuid

import pytest

from app.sandbox.local_driver import LocalSandboxDriver

GIT_PRESENT = shutil.which("git") is not None

pytestmark = pytest.mark.skipif(not GIT_PRESENT, reason="git not installed on this host")


class _FakeMission:
    id = "fake-mission"

    class repo:  # noqa: N801
        pack = "__no_such_pack__"
        language_runtime = "node20"


@pytest.mark.asyncio
async def test_local_sandbox_full_lifecycle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))

    # Bust the cached settings so the new env wins.
    from app.config import get_settings

    get_settings.cache_clear()

    driver = LocalSandboxDriver()
    mission = _FakeMission()
    session_id = uuid.uuid4()
    handle = await driver.provision(mission, session_id)

    try:
        # 1) run echo
        result = await driver.run(handle, ["/bin/echo", "hello"])
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"

        # 2) write/read roundtrip
        await driver.write_file(handle, "foo.txt", b"baz\n")
        content = await driver.read_file(handle, "foo.txt")
        assert content == b"baz\n"

        # 3) apply a tiny diff cleanly
        await driver.write_file(handle, "hello.txt", b"line one\nline two\n")
        # commit the new file so the diff applies cleanly
        await driver.run(handle, ["git", "add", "hello.txt"])
        await driver.run(handle, ["git", "commit", "-q", "-m", "add"])

        diff = (
            "--- a/hello.txt\n+++ b/hello.txt\n@@ -1,2 +1,3 @@\n line one\n line two\n+line three\n"
        )
        apply = await driver.apply_diff(handle, diff)
        assert apply.applied, apply.error
        # File contents now have 3 lines.
        content = await driver.read_file(handle, "hello.txt")
        assert content.count(b"\n") == 3

        # 4) diff_from_initial returns non-empty after the change.
        out = await driver.diff_from_initial(handle)
        assert "hello.txt" in out
    finally:
        await driver.destroy(handle)

    # 5) destroyed: workdir gone.
    assert not handle.workdir.exists()


class _AuthMission:
    """Stand-in manifest pointed at the fullstack-auth-demo repo pack."""

    id = "auth-cookie-expiration"

    class repo:  # noqa: N801
        pack = "fullstack-auth-demo"
        language_runtime = "node20"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_sandbox_with_real_repo_pack(tmp_path, monkeypatch) -> None:
    """Smoke-test the M2 exit gate: copy the real repo pack into a sandbox,
    git-init it, and confirm a baseline file is reachable.

    Marked ``integration`` because it touches the on-disk repo pack. It runs
    without network because no setup_commands are invoked here (M2 local
    fallback contract — see plan §9.4).
    """
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    pack_root = settings.missions_root / "_shared" / "repos" / "fullstack-auth-demo"
    if not pack_root.exists():
        pytest.skip("fullstack-auth-demo repo pack not present in this checkout")

    driver = LocalSandboxDriver()
    mission = _AuthMission()
    session_id = uuid.uuid4()
    handle = await driver.provision(mission, session_id)
    try:
        # The repo pack ships a top-level package.json.
        pkg = await driver.read_file(handle, "package.json")
        assert b'"fullstack-auth-demo"' in pkg

        # The session.ts file in the pack contains the isValid helper the
        # mission references.
        session_ts = await driver.read_file(handle, "backend/src/auth/session.ts")
        assert b"isValid" in session_ts
    finally:
        await driver.destroy(handle)
