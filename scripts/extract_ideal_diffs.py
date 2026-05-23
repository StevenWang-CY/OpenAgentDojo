#!/usr/bin/env python3
"""Extract every ```diff``` fenced block from each mission's
``ideal_solution.md`` and concatenate them into ``ideal_solution.diff``.

Idempotent: re-running overwrites the .diff with the freshly-extracted
content. Skips missions that already ship a hand-authored .diff (none
do today, but the safeguard means an author can override the
extraction).

P0-2 — every non-tutorial mission must ship ``ideal_solution.diff`` so
the post-mortem walkthrough's three-way diff has a canonical "what was
expected" layer. The validator (apps/api/scripts/validate_missions.py)
enforces presence after this script runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MISSIONS_ROOT = REPO_ROOT / "missions"

# Match ```diff ... ``` fenced blocks. ``re.DOTALL`` so the body can
# contain newlines.
_FENCE_RE = re.compile(r"```diff\n(.*?)\n```", re.DOTALL)


def extract_diffs(mission_dir: Path) -> str | None:
    md = mission_dir / "ideal_solution.md"
    if not md.exists():
        return None
    body = md.read_text(encoding="utf-8")
    blocks = _FENCE_RE.findall(body)
    if not blocks:
        return None
    # Concatenate with one blank line between blocks. ``--- a/`` headers
    # at the start of each block remain intact so ``git apply --check``
    # treats it as a multi-file unified diff.
    joined = "\n".join(b.rstrip() + "\n" for b in blocks)
    return joined


def main() -> int:
    if not MISSIONS_ROOT.exists():
        print(f"missions root not found at {MISSIONS_ROOT}", file=sys.stderr)
        return 2
    written = 0
    skipped = 0
    for mission_dir in sorted(MISSIONS_ROOT.iterdir()):
        if not mission_dir.is_dir():
            continue
        if mission_dir.name.startswith("_"):
            continue
        # Skip tutorial missions (kind=tutorial) by looking for an
        # explicit ``kind: tutorial`` line in the manifest. The tutorial
        # ships its own hand-authored diff; we'd overwrite it.
        manifest = mission_dir / "mission.yaml"
        is_tutorial = (
            manifest.exists()
            and "kind: tutorial" in manifest.read_text(encoding="utf-8")
        )
        if is_tutorial:
            print(f"SKIP (tutorial)  {mission_dir.name}")
            skipped += 1
            continue
        diff = extract_diffs(mission_dir)
        if diff is None:
            print(f"SKIP (no diffs)  {mission_dir.name}")
            skipped += 1
            continue
        out = mission_dir / "ideal_solution.diff"
        out.write_text(diff, encoding="utf-8")
        print(f"WROTE {out.relative_to(REPO_ROOT)} ({len(diff)} bytes)")
        written += 1
    print(f"\n{written} written, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
