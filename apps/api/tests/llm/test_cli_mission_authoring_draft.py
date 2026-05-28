"""P1-1 — smoke tests for ``apps/api/app/llm/cli.py``.

The CLI is a contributor accelerator: it shells out to the model and
writes a ``_draft/`` tree alongside an existing mission folder. The
following invariants are load-bearing:

* The argparse surface is reachable — ``python -m app.llm.cli --help``
  exits 0 (no Python import errors leaking to the contributor's TTY).

* ``generate_mission_authoring_draft`` (the importable API) writes the
  expected file tree when given a fake client and refuses to silently
  clobber an existing ``_draft/`` directory.

* The mission loader's ``_draft/`` rejection (covered separately in
  ``test_loader_rejects_draft_dir.py``) trips on the tree this CLI
  produces — so the end-to-end contract holds: scaffold → draft →
  loader refuses → author promotes → loader accepts.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm import cli as llm_cli
from app.missions.loader import MissionLoader
from app.missions.manifest import MissionConfigError

_API_DIR = Path(__file__).resolve().parents[2]


class _FakeClient:
    """Minimal Anthropic-shaped stub that returns a fixed text block."""

    def __init__(self, text: str = "TITLE: Drafted mission title\n\nFAILURE MODE:\nThe agent leaves a bug.") -> None:
        self._text = text
        self.last_kwargs: dict | None = None

    async def messages_create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


def test_cli_help_exits_zero() -> None:
    """``python -m app.llm.cli --help`` must succeed (argparse smoke test).

    Run as a subprocess so we exercise the same import path a contributor
    would hit on the command line — no test-time monkeypatching can hide
    a missing dependency this way.
    """
    result = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-m", "app.llm.cli", "--help"],
        cwd=str(_API_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"--help exited {result.returncode}; stderr=\n{result.stderr}"
    )
    assert "mission-authoring-draft" in result.stdout


def test_generate_draft_writes_expected_tree(tmp_path: Path) -> None:
    """The importable API writes the documented draft file set."""
    mission_dir = tmp_path / "99-fake-mission"
    mission_dir.mkdir()
    fake = _FakeClient()
    draft_dir = asyncio.run(
        llm_cli.generate_mission_authoring_draft(
            mission_dir=mission_dir,
            repo_pack_id="fullstack-auth-demo",
            failure_mode_title="Race condition",
            seed_outline="login then fetch profile",
            client=fake,
        )
    )
    assert draft_dir == mission_dir / "_draft"
    assert (draft_dir / "ideal_solution.draft.md").read_text(
        encoding="utf-8"
    ).startswith("TITLE:")
    assert (draft_dir / "ideal_solution.draft.diff").exists()
    assert (draft_dir / "agent_patch.draft.diff").exists()
    assert (draft_dir / "prompts" / "response.draft.md").exists()
    # Language extension follows the repo pack.
    assert (draft_dir / "hidden_tests" / "auth.hidden.draft.ts").exists()

    # The model id was passed through to the client.
    assert fake.last_kwargs is not None
    assert fake.last_kwargs.get("model") == "claude-opus-4-7"


def test_generate_draft_refuses_to_clobber_existing(tmp_path: Path) -> None:
    mission_dir = tmp_path / "99-fake-mission"
    (mission_dir / "_draft").mkdir(parents=True)
    fake = _FakeClient()
    with pytest.raises(FileExistsError):
        asyncio.run(
            llm_cli.generate_mission_authoring_draft(
                mission_dir=mission_dir,
                repo_pack_id="data-api-demo",
                failure_mode_title="X",
                seed_outline="Y",
                client=fake,
            )
        )


def test_generate_draft_requires_mission_dir(tmp_path: Path) -> None:
    fake = _FakeClient()
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            llm_cli.generate_mission_authoring_draft(
                mission_dir=tmp_path / "does-not-exist",
                repo_pack_id="data-api-demo",
                failure_mode_title="X",
                seed_outline="Y",
                client=fake,
            )
        )


def test_draft_tree_then_loader_rejects(tmp_path: Path) -> None:
    """Drafted tree must be refused by the loader (end-to-end contract).

    We synthesise a minimal mission folder around the drafted tree and
    confirm ``MissionLoader.validate_all`` raises — proves the scaffold
    output is not accidentally shippable.
    """
    missions_root = tmp_path / "missions"
    missions_root.mkdir()
    mission_dir = missions_root / "99-fake"
    mission_dir.mkdir()
    # Minimal mission.yaml — content does not matter because the
    # ``_draft/`` gate fires before pydantic validation.
    (mission_dir / "mission.yaml").write_text(
        "id: fake-mission\n", encoding="utf-8"
    )

    fake = _FakeClient()
    asyncio.run(
        llm_cli.generate_mission_authoring_draft(
            mission_dir=mission_dir,
            repo_pack_id="fullstack-auth-demo",
            failure_mode_title="X",
            seed_outline="Y",
            client=fake,
        )
    )

    loader = MissionLoader(missions_root)
    with pytest.raises(MissionConfigError) as excinfo:
        loader.validate_all()
    assert "_draft" in str(excinfo.value)
