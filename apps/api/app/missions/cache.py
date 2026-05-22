"""In-process manifest cache shared by the missions/sessions/agent routers.

The mission tree is read-only at runtime (content updates trigger a redeploy /
loader re-run), so scanning the YAML once per request is wasted work. The
cache key is a content hash of every ``mission.yaml`` under the configured
root so a ``git checkout`` (which keeps file mtime stable on some configs)
still invalidates the cache without a server restart.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import get_settings
from app.missions.loader import LoadedMission, MissionLoader

_MANIFEST_CACHE: dict[tuple[str, str], dict[str, LoadedMission]] = {}


def _aggregate_fingerprint(root: Path) -> str:
    """Return a content fingerprint across every mission.yaml under ``root``."""
    if not root.exists():
        return ""
    h = hashlib.blake2b(digest_size=16)
    try:
        for yaml in sorted(root.glob("*/mission.yaml")):
            try:
                h.update(yaml.read_bytes())
                h.update(yaml.name.encode("utf-8"))
            except OSError:
                continue
    except OSError:
        return ""
    return h.hexdigest()


def cached_manifests() -> dict[str, LoadedMission]:
    """Return ``{mission_id: LoadedMission}`` for the configured missions root.

    Caches by (root, content-hash). On scan failure logs at WARN and returns
    an empty mapping so callers can fall back to DB-only data.
    """
    settings = get_settings()
    root_path: Path = settings.missions_root
    root_str = str(root_path)
    fingerprint = _aggregate_fingerprint(root_path)
    key = (root_str, fingerprint)

    cached = _MANIFEST_CACHE.get(key)
    if cached is not None:
        return cached

    loader = MissionLoader(root_path)
    try:
        loaded = loader.scan()
    except Exception as exc:
        logger.warning("manifest scan failed for {}: {}", root_str, exc)
        return {}

    fresh = {m.manifest.id: m for m in loaded}
    for k in [k for k in _MANIFEST_CACHE if k[0] == root_str]:
        _MANIFEST_CACHE.pop(k, None)
    _MANIFEST_CACHE[key] = fresh
    return fresh


def detail_extras_for(loaded: LoadedMission | None) -> dict[str, Any]:
    """Return the manifest-derived fields merged into MissionDetail responses."""
    if loaded is None:
        return {
            "brief": "",
            "short_description": "",
            "language_runtime": None,
            "visible_tests": [],
            "expected_context_required": [],
            "expected_context_recommended": [],
            "expected_diff_lines_p50": None,
        }
    m = loaded.manifest
    return {
        "brief": m.brief,
        "short_description": m.short_description,
        "language_runtime": m.repo.language_runtime,
        "visible_tests": list(m.visible_tests),
        "expected_context_required": list(m.expected_context.required),
        "expected_context_recommended": list(m.expected_context.recommended),
        "expected_diff_lines_p50": m.expected_diff_lines_p50,
    }


__all__ = ["cached_manifests", "detail_extras_for"]
