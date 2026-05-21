"""Docker driver provisions containers with the hardened kwargs.

We mock ``docker.client.containers.create`` and assert the exact security
posture spelled out in IMPLEMENTATION_PLAN.md §§9, 21:

* ``security_opt`` includes ``no-new-privileges:true`` *and* a seccomp profile
  whose path resolves under ``infra/docker/seccomp.json``.
* ``read_only=True`` (root filesystem locked).
* ``tmpfs`` carries sized, uid-pinned mounts for ``/tmp`` (128m) and the
  writable ``/workspace`` (1g).
* ``pids_limit=256``.
* ``user="1000:1000"``.
* ``cap_drop=["ALL"]`` and ``network_disabled=True`` still present.
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest

# Skip the whole module if the docker SDK isn't installed (CI test-py uses it,
# but light-touch dev environments may not).
docker = pytest.importorskip("docker")


class _Container:
    """Stand-in for a docker SDK Container — enough surface for the driver."""

    id = "deadbeefcafefade000000000000000000000000000000000000000000000000"

    def start(self) -> None:  # pragma: no cover — invoked but not asserted
        pass


class _Containers:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Container()

    def get(self, _cid):  # pragma: no cover
        return _Container()


class _FakeClient:
    def __init__(self) -> None:
        self.containers = _Containers()
        # The driver's _ensure_client also calls .ping().

    def ping(self) -> bool:
        return True


@pytest.fixture
def fake_docker(monkeypatch) -> _FakeClient:
    """Patch ``docker.from_env`` so the driver sees our recording client."""
    client = _FakeClient()

    fake_mod = types.SimpleNamespace(from_env=lambda: client)
    monkeypatch.setitem(sys.modules, "docker", fake_mod)
    return client


class _Mission:
    id = "auth-cookie-expiration"

    class repo:  # noqa: N801
        language_runtime = "node20"


@pytest.mark.asyncio
async def test_docker_provision_applies_hardening_kwargs(
    fake_docker: _FakeClient, tmp_path: Path, monkeypatch
) -> None:
    # Import after monkeypatching so the lazy `import docker` inside the driver
    # picks up our shim.
    from app.sandbox.docker_driver import DockerSandboxDriver

    driver = DockerSandboxDriver()
    sid = uuid.uuid4()
    await driver.provision(_Mission(), sid)

    kwargs = fake_docker.containers.kwargs
    assert kwargs is not None, "containers.create was not called"

    # ---- core isolation flags ----
    assert kwargs["network_disabled"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["read_only"] is True
    assert kwargs["pids_limit"] == 256
    assert kwargs["user"] == "1000:1000"

    # ---- security_opt ----
    sec_opt = kwargs["security_opt"]
    assert "no-new-privileges:true" in sec_opt
    seccomp_entries = [s for s in sec_opt if s.startswith("seccomp=")]
    assert len(seccomp_entries) == 1, sec_opt
    seccomp_path = Path(seccomp_entries[0].split("=", 1)[1])
    assert seccomp_path.name == "seccomp.json"
    # The ASYNC240 lint warns about pathlib in async — fine here, the test is
    # checking a static file fixture rather than performing real I/O.
    assert seccomp_path.exists(), (  # noqa: ASYNC240
        f"shipped seccomp profile missing at {seccomp_path}"
    )

    # ---- tmpfs ----
    tmpfs = kwargs["tmpfs"]
    assert "/tmp" in tmpfs and "size=128m" in tmpfs["/tmp"] and "uid=1000" in tmpfs["/tmp"]
    assert "/workspace" in tmpfs and "size=1g" in tmpfs["/workspace"]
    assert "gid=1000" in tmpfs["/workspace"]

    # ---- labels carry session + mission ----
    labels = kwargs["labels"]
    assert labels["arena.session_id"] == str(sid)
    assert labels["arena.mission_id"] == "auth-cookie-expiration"
