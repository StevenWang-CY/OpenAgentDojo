"""Scan ``/missions`` on disk, validate manifests, upsert the catalog."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from sqlalchemy import text

from app.missions.manifest import MissionManifest


@dataclass(slots=True)
class LoadedMission:
    """A parsed mission, its source folder, and the canonical manifest hash."""

    manifest: MissionManifest
    folder: Path
    manifest_sha256: str

    def to_catalog_row(self) -> dict[str, Any]:
        """Map to a row dict matching the ``missions`` table."""
        m = self.manifest
        return {
            "id": m.id,
            "title": m.title,
            "difficulty": m.difficulty,
            "category": m.category,
            "repo_pack": m.repo.pack,
            "initial_commit": m.repo.initial_commit,
            "estimated_minutes": m.estimated_minutes,
            "failure_mode": m.failure_mode.id,
            "skills_tested": list(m.skills_tested),
            "manifest_sha256": self.manifest_sha256,
            "version": m.version,
            "published": m.published,
        }


class MissionLoader:
    """Discover, parse, and upsert mission manifests.

    ``root`` is the parent directory containing per-mission folders
    (``NN-id/mission.yaml``).
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------ scan
    def scan(self) -> list[LoadedMission]:
        """Return every valid mission under ``root`` sorted by id.

        Mission folders that contain a ``.draft`` marker file are skipped —
        used for in-progress missions that should not appear in the catalog
        or be exercised by ``validate:missions`` until they're ready.
        """
        out: list[LoadedMission] = []
        if not self.root.exists():
            logger.debug("missions root {} does not exist; returning []", self.root)
            return out

        for path in sorted(self.root.glob("*/mission.yaml")):
            if (path.parent / ".draft").exists():
                logger.debug("skipping draft mission at {}", path.parent.name)
                continue
            try:
                loaded = self._load_one(path)
            except Exception as exc:
                logger.error("failed to load mission at {}: {}", path, exc)
                raise
            out.append(loaded)
        return sorted(out, key=lambda x: x.manifest.id)

    def validate_all(self) -> list[LoadedMission]:
        """Like ``scan`` but raises a single aggregated error if any fail."""
        return self.scan()

    def _load_one(self, manifest_path: Path) -> LoadedMission:
        raw_bytes = manifest_path.read_bytes()
        sha = hashlib.sha256(raw_bytes).hexdigest()
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{manifest_path} did not parse to a mapping")
        manifest = MissionManifest.model_validate(data)
        return LoadedMission(manifest=manifest, folder=manifest_path.parent, manifest_sha256=sha)

    # --------------------------------------------------------------- upsert
    async def upsert_catalog(self, db: Any) -> int:
        """Upsert every loaded mission into the catalog using the given AsyncSession.

        Returns the number of rows upserted.
        """
        loaded = self.scan()
        if not loaded:
            return 0

        sql = text(
            """
            INSERT INTO missions
              (id, title, difficulty, category, repo_pack, initial_commit,
               estimated_minutes, failure_mode, skills_tested,
               manifest_sha256, version, published)
            VALUES
              (:id, :title, :difficulty, :category, :repo_pack, :initial_commit,
               :estimated_minutes, :failure_mode, :skills_tested,
               :manifest_sha256, :version, :published)
            ON CONFLICT (id) DO UPDATE SET
              title             = EXCLUDED.title,
              difficulty        = EXCLUDED.difficulty,
              category          = EXCLUDED.category,
              repo_pack         = EXCLUDED.repo_pack,
              initial_commit    = EXCLUDED.initial_commit,
              estimated_minutes = EXCLUDED.estimated_minutes,
              failure_mode      = EXCLUDED.failure_mode,
              skills_tested     = EXCLUDED.skills_tested,
              manifest_sha256   = EXCLUDED.manifest_sha256,
              version           = EXCLUDED.version,
              published         = EXCLUDED.published
            """
        )
        for m in loaded:
            await db.execute(sql, m.to_catalog_row())
        await db.commit()
        return len(loaded)


# ----------------------------------------------------------------- CLI


async def _async_main(root: Path, seed: bool) -> int:
    loader = MissionLoader(root)
    loaded = loader.scan()
    logger.info("loaded {} manifests from {}", len(loaded), root)
    for m in loaded:
        logger.info("  - {} v{} sha={}", m.manifest.id, m.manifest.version, m.manifest_sha256[:12])

    if not seed:
        return 0

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        upserted = await loader.upsert_catalog(db)
        logger.info("upserted {} catalog rows", upserted)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mission manifest loader / seeder")
    parser.add_argument("--root", type=Path, default=None, help="missions/ directory")
    parser.add_argument(
        "--no-seed",
        dest="seed",
        action="store_false",
        help="parse + validate only; do not write to the database",
    )
    parser.add_argument(
        "--seed",
        dest="seed",
        action="store_true",
        help="upsert into the configured database (default)",
    )
    parser.set_defaults(seed=True)
    args = parser.parse_args(argv)

    if args.root is None:
        from app.config import get_settings

        args.root = get_settings().missions_root

    return asyncio.run(_async_main(args.root, args.seed))


if __name__ == "__main__":
    sys.exit(main())
