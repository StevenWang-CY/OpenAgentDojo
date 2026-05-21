"""Deterministic mission-folder resolution.

Several call sites (submit, reports, agent) used to hand-roll a glob like
``missions/glob('*-{mission_id}') + missions/glob(mission_id)`` to find the
on-disk folder backing a mission id. That logic had two problems:

* It silently accepted *multiple* matches, which would non-deterministically
  pick whichever the filesystem returned first — a real risk on shared
  mission packs where ``01-foo`` and ``99-foo`` could both exist during a
  rename.
* It treated the absence of a ``mission.yaml`` as "fine", deferring the real
  error to the loader (which then surfaced an obscure traceback).

:func:`resolve_mission_dir` consolidates the lookup with strict semantics:

* Prefer numbered folders matching ``^\\d+-{mission_id}$`` (anchored).
* If multiple numbered folders match, raise :class:`ValueError` — callers
  should investigate rather than silently grade against the wrong sandbox.
* Fall back to the exact folder name (``missions/{mission_id}``) when no
  numbered folder is present.
* Raise :class:`MissionFolderNotFoundError` (a :class:`FileNotFoundError`
  subclass) when neither shape exists.
"""

from __future__ import annotations

import re
from pathlib import Path


class MissionFolderNotFoundError(FileNotFoundError):
    """Raised when no mission folder matches the given id.

    Subclassing :class:`FileNotFoundError` keeps the historical
    ``except FileNotFoundError`` callers working while allowing the resolver
    to raise something more specific where it matters.
    """


def resolve_mission_dir(missions_root: Path, mission_id: str) -> Path:
    """Return the on-disk folder backing ``mission_id``.

    Parameters
    ----------
    missions_root:
        The parent directory containing the per-mission folders.
    mission_id:
        The canonical mission id (e.g. ``"auth-cookie-expiration"``). May
        contain regex metacharacters in pathological cases — we escape it
        before building the prefix regex.

    Raises
    ------
    MissionFolderNotFoundError
        When no ``NN-{mission_id}`` or exact-name folder exists.
    ValueError
        When more than one numbered folder matches the same mission id.
    """
    if not isinstance(missions_root, Path):
        missions_root = Path(missions_root)

    if not missions_root.exists() or not missions_root.is_dir():
        raise MissionFolderNotFoundError(
            f"missions root does not exist: {missions_root}"
        )

    safe = re.escape(mission_id)
    pattern = re.compile(rf"^\d+-{safe}$")
    numbered = sorted(
        p for p in missions_root.iterdir() if p.is_dir() and pattern.match(p.name)
    )
    if numbered:
        if len(numbered) > 1:
            raise ValueError(
                f"ambiguous mission folder for {mission_id!r}: "
                f"{[p.name for p in numbered]}"
            )
        return numbered[0]

    exact = missions_root / mission_id
    if exact.is_dir():
        return exact

    raise MissionFolderNotFoundError(
        f"mission folder not found for {mission_id!r} under {missions_root}"
    )


__all__ = ["MissionFolderNotFoundError", "resolve_mission_dir"]
