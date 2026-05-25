"""Phase 4.A.T1 — alembic must resolve to exactly one head.

The 0021 (github_oauth) and 0022 (session_mode) migrations both rooted
on 0020, leaving two heads. The 0023 merge migration reconvenes them;
without it, any future migration would have to pick a parent and
silently drop the other branch's history. This test parses
``alembic heads`` output and asserts the chain ends with exactly one
revision.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not (_API_DIR / "alembic.ini").exists(),
    reason="alembic.ini missing — likely not running inside apps/api",
)
def test_alembic_heads_resolves_to_single_revision() -> None:
    """``alembic heads`` must print exactly one revision (Phase 4.A.T1)."""
    # Run alembic via ``uv run`` so the venv is correctly resolved
    # regardless of which interpreter pytest itself is using. The
    # command is read-only and doesn't touch any DB.
    result = subprocess.run(
        ["uv", "run", "alembic", "heads"],
        cwd=str(_API_DIR),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic heads failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # ``alembic heads`` prints one revision per line; with a single
    # head we expect exactly one non-empty line (plus the optional
    # ``(head)`` tag). Filter blank lines so a stray newline doesn't
    # flunk the assertion.
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("warning:")
    ]
    assert len(lines) == 1, f"expected exactly one alembic head, got {len(lines)}:\n" + "\n".join(
        lines
    )
