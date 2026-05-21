"""Locate base repo packs on disk.

A repo pack is a frozen project tree (e.g. ``fullstack-auth-demo``) that lives
under ``missions/_shared/repos/<pack>``. For the docker driver, the pack is
baked into the image; for the local driver, it is copied into the sandbox
workdir at provision time.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.config import get_settings


class RepoPackNotFoundError(FileNotFoundError):
    """Raised when a mission references a repo pack that is not on disk."""


def load_repo_pack(pack_name: str) -> Path:
    """Return the on-disk path for a named repo pack.

    Raises :class:`RepoPackNotFoundError` if the directory does not exist.
    """
    settings = get_settings()
    root = settings.missions_root / "_shared" / "repos" / pack_name
    if not root.exists():
        # Be diagnostic rather than mysterious — testers often misplace packs.
        logger.error("repo pack {} not found at {}", pack_name, root)
        raise RepoPackNotFoundError(f"repo pack '{pack_name}' not found at {root}")
    return root
