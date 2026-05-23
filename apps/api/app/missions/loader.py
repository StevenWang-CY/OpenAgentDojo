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

from app.missions.manifest import MissionConfigError, MissionManifest


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
            "kind": m.kind,
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

        A single broken manifest must NOT zero the catalog. ``scan`` collects
        every mission it can parse and logs the rest; ``validate_all`` is the
        strict variant that callers (the validate-missions CLI, pre-commit
        hooks, contract tests) reach for when they need a hard failure.
        """
        loaded, _ = self._scan_collecting_failures()
        return loaded

    def _scan_collecting_failures(self) -> tuple[list[LoadedMission], list[tuple[Path, Exception]]]:
        """Internal helper that also returns the per-mission failure list."""
        out: list[LoadedMission] = []
        failures: list[tuple[Path, Exception]] = []
        if not self.root.exists():
            logger.debug("missions root {} does not exist; returning []", self.root)
            return out, failures

        for path in sorted(self.root.glob("*/mission.yaml")):
            if (path.parent / ".draft").exists():
                logger.debug("skipping draft mission at {}", path.parent.name)
                continue
            try:
                loaded = self._load_one(path)
            except Exception as exc:
                logger.error("failed to load mission at {}: {}", path, exc)
                failures.append((path, exc))
                continue
            out.append(loaded)
        return sorted(out, key=lambda x: x.manifest.id), failures

    def validate_all(self) -> list[LoadedMission]:
        """Strict scan — raise an aggregated error if any mission fails."""
        loaded, failures = self._scan_collecting_failures()
        if failures:
            joined = "; ".join(f"{p}: {exc}" for p, exc in failures)
            raise MissionConfigError(f"mission load failures: {joined}")
        return loaded

    def load_manifest(self, manifest_path: Path) -> LoadedMission:
        """Load a single ``mission.yaml`` into a :class:`LoadedMission`.

        Public surface promoted from the legacy ``_load_one`` so external
        callers don't reach into a private name (P2-B3). ``_load_one`` is
        retained as an alias for back-compat with tests.

        Raises :class:`MissionConfigError` with a path-annotated message when
        a hard mission invariant fails (e.g. visible tests declared with no
        matching ``test_commands``) — surfacing the violation at load time
        rather than letting it leak free points into the grading engine.
        """
        raw_bytes = manifest_path.read_bytes()
        sha = hashlib.sha256(raw_bytes).hexdigest()
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{manifest_path} did not parse to a mapping")
        try:
            manifest = MissionManifest.model_validate(data)
        except MissionConfigError as exc:
            # Surface the offending file in the message so authors get a
            # one-line, copy-pasteable pointer rather than a stack trace
            # without context.
            raise MissionConfigError(f"{manifest_path}: {exc}") from exc
        return LoadedMission(manifest=manifest, folder=manifest_path.parent, manifest_sha256=sha)

    # Back-compat alias for the historical private name.
    _load_one = load_manifest

    # --------------------------------------------------------------- upsert
    async def upsert_catalog(self, db: Any) -> int:
        """Upsert every loaded mission into the catalog using the given AsyncSession.

        Returns the number of rows upserted. Uses ``ON CONFLICT`` for both
        Postgres and SQLite (both support the same syntax for INSERT ... ON
        CONFLICT (id) DO UPDATE since SQLite 3.24) and falls back to a
        SELECT-then-UPDATE/INSERT pair for any other dialect so tests stay
        portable.
        """
        loaded = self.scan()
        if not loaded:
            return 0

        dialect = db.bind.dialect.name if hasattr(db, "bind") and db.bind is not None else None
        if dialect is None:
            engine = getattr(db, "get_bind", lambda: None)()
            if engine is not None:
                dialect = engine.dialect.name

        if dialect in {"postgresql", "sqlite"}:
            sql = text(
                """
                INSERT INTO missions
                  (id, title, difficulty, category, repo_pack, initial_commit,
                   estimated_minutes, failure_mode, skills_tested,
                   manifest_sha256, version, published, kind)
                VALUES
                  (:id, :title, :difficulty, :category, :repo_pack, :initial_commit,
                   :estimated_minutes, :failure_mode, :skills_tested,
                   :manifest_sha256, :version, :published, :kind)
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
                  published         = EXCLUDED.published,
                  kind              = EXCLUDED.kind
                """
            )
            for m in loaded:
                await db.execute(sql, m.to_catalog_row())
        else:
            # Portable fallback: emulate UPSERT via SELECT + INSERT/UPDATE.
            select_sql = text("SELECT id FROM missions WHERE id = :id")
            insert_sql = text(
                """
                INSERT INTO missions
                  (id, title, difficulty, category, repo_pack, initial_commit,
                   estimated_minutes, failure_mode, skills_tested,
                   manifest_sha256, version, published, kind)
                VALUES
                  (:id, :title, :difficulty, :category, :repo_pack, :initial_commit,
                   :estimated_minutes, :failure_mode, :skills_tested,
                   :manifest_sha256, :version, :published, :kind)
                """
            )
            update_sql = text(
                """
                UPDATE missions SET
                  title=:title, difficulty=:difficulty, category=:category,
                  repo_pack=:repo_pack, initial_commit=:initial_commit,
                  estimated_minutes=:estimated_minutes, failure_mode=:failure_mode,
                  skills_tested=:skills_tested, manifest_sha256=:manifest_sha256,
                  version=:version, published=:published, kind=:kind
                WHERE id=:id
                """
            )
            for m in loaded:
                row = m.to_catalog_row()
                exists = (await db.execute(select_sql, {"id": row["id"]})).first()
                await db.execute(update_sql if exists else insert_sql, row)
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
