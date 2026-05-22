"""Local sandbox driver's freeze_and_grade end-to-end.

Verifies the real pipeline (no MVP shim): snapshot diff, copy hidden tests,
run visible + hidden suites, parse counts, return GradingArtifacts.
"""

from __future__ import annotations

import shutil
import textwrap
import uuid
from pathlib import Path

import pytest

from app.sandbox.local_driver import LocalSandboxDriver

GIT_PRESENT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not GIT_PRESENT, reason="git not installed")


class _FakeMission:
    id = "fake"

    class repo:  # noqa: N801
        pack = "__no_such_pack__"
        language_runtime = "node20"
        workdir = "/workspace"
        test_commands = {"unit": 'echo \'{"passed": 2, "failed": 0, "skipped": 0}\'; exit 0'}

    class hidden_tests:  # noqa: N801
        # The runner.sh script we install below prints a JSON envelope.
        command = "bash grader/hidden_tests/runner.sh"


def _write_hidden_tests(manifest_folder: Path) -> None:
    hidden = manifest_folder / "hidden_tests"
    hidden.mkdir(parents=True)
    runner = hidden / "runner.sh"
    runner.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -e
            cat <<'EOF'
            {"passed": 3, "failed": 0, "skipped": 0}
            EOF
            """
        ),
        encoding="utf-8",
    )
    runner.chmod(0o755)
    # Marker file we'll assert was copied into the sandbox.
    (hidden / "fixture.txt").write_text("mounted", encoding="utf-8")


@pytest.mark.asyncio
async def test_freeze_and_grade_real_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path / "sandbox"))
    from app.config import get_settings

    get_settings.cache_clear()

    # Lay out the mission folder (mission_folder / hidden_tests / runner.sh).
    manifest_folder = tmp_path / "01-fake"
    manifest_folder.mkdir()
    _write_hidden_tests(manifest_folder)

    driver = LocalSandboxDriver()
    mission = _FakeMission()
    handle = await driver.provision(mission, uuid.uuid4())

    try:
        # Make a real diff in the sandbox so artifacts.diff is non-empty.
        await driver.write_file(handle, "src.txt", b"hello\n")
        await driver.run(handle, ["git", "add", "src.txt"])
        await driver.run(handle, ["git", "commit", "-q", "-m", "seed"])
        await driver.write_file(handle, "src.txt", b"hello\nworld\n")

        artifacts = await driver.freeze_and_grade(handle, mission, manifest_folder=manifest_folder)

        # Diff snapshot present.
        assert "src.txt" in artifacts.diff
        assert "world" in artifacts.diff

        # Visible "unit" suite captured with our exit-0 placeholder.
        unit = artifacts.test_results["unit"]
        assert unit["exit_code"] == 0
        assert unit["passed"] == 2  # parsed from "passed=2"

        # Hidden suite parsed the JSON envelope.
        hidden = artifacts.test_results["hidden"]
        assert hidden["exit_code"] == 0
        assert hidden["passed"] == 3
        assert hidden["failed"] == 0

        # Hidden tests were copied into the sandbox.
        copied = handle.workdir / "grader" / "hidden_tests" / "fixture.txt"
        assert copied.exists()
        assert copied.read_text() == "mounted"
    finally:
        await driver.destroy(handle)


@pytest.mark.asyncio
async def test_freeze_and_grade_test_phase_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path / "sandbox"))
    from app.config import get_settings

    get_settings.cache_clear()

    manifest_folder = tmp_path / "01-fake"
    manifest_folder.mkdir()
    _write_hidden_tests(manifest_folder)

    class _SlowMission(_FakeMission):
        class repo(_FakeMission.repo):  # noqa: N801
            test_commands = {"unit": "sleep 5"}

    driver = LocalSandboxDriver()
    handle = await driver.provision(_SlowMission(), uuid.uuid4())
    try:
        # Patch the driver's _run_test_phase to use a 1-second cap.
        original = driver._run_test_phase

        async def _short(handle, suite, cmd, timeout_s):
            return await original(handle, suite, cmd, timeout_s=1)

        driver._run_test_phase = _short  # type: ignore[method-assign]

        artifacts = await driver.freeze_and_grade(
            handle, _SlowMission(), manifest_folder=manifest_folder
        )
        unit = artifacts.test_results["unit"]
        assert unit["timed_out"] is True
        assert unit["exit_code"] >= 1
    finally:
        await driver.destroy(handle)
