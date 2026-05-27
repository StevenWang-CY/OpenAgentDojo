"""Public roadmap placeholders — parsed from ``roadmap.yaml`` (P1-1).

The catalog endpoint reads the roadmap when the client passes
``?include=upcoming``; each entry renders as a muted ``coming_soon``
card with a dated chip so returning users see *what is next*, not just
*what has shipped*.

The loader is intentionally tiny: a Pydantic model, a file read, a
cache. It rejects past-dated placeholders so the roadmap can never
drift into a "we forgot to push this date" state, and it provides
:func:`filter_collisions` so callers with knowledge of the shipped
mission catalogue can drop placeholders whose ids collide.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# The roadmap lives next to ``manifest.py`` so editors find it without
# hunting through ``apps/api/app``. The deployed image bundles
# ``apps/api/app/missions/roadmap.yaml`` as a sibling resource.
_ROADMAP_PATH: Path = Path(__file__).resolve().parent / "roadmap.yaml"

RoadmapLanguage = Literal["typescript", "python", "go"]


class RoadmapPlaceholder(BaseModel):
    """One dated, language-tagged ``coming_soon`` entry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$", min_length=3, max_length=64)
    title: str = Field(min_length=5, max_length=120)
    target_release_date: date
    teaser: str = Field(min_length=10, max_length=200)
    language: RoadmapLanguage

    @field_validator("target_release_date")
    @classmethod
    def _date_must_be_future(cls, v: date) -> date:
        # Past-dated placeholders read as vapour; reject them at load time
        # so the roadmap surface never drifts into a "we forgot" state.
        # The check is intentionally permissive on the day itself: a
        # placeholder dated for *today* still reads as "any moment now"
        # rather than as already-late. Compute "today" via
        # ``datetime.now(UTC).date()`` rather than ``date.today()`` so
        # tests that freeze the clock (and the deployed timezone) see a
        # deterministic boundary — naive ``date.today()`` reads from the
        # host timezone and can drift across a TZ-shifted CI runner.
        if v < datetime.now(UTC).date():
            raise ValueError(
                f"target_release_date {v.isoformat()} is in the past; "
                "update or remove the placeholder."
            )
        return v


class Roadmap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placeholders: list[RoadmapPlaceholder] = Field(default_factory=list)


# Cache holder — a single-slot mutable container so the loader can refresh
# the cached entry without reaching for ``global``. Layout: ``[(mtime, Roadmap)]``
# (empty list = unset).
_ROADMAP_CACHE: list[tuple[float, Roadmap]] = []


def load_roadmap(path: Path | None = None) -> Roadmap:
    """Read and validate the roadmap YAML.

    Caches by mtime so the parsed model is reused across requests. The
    loader returns an empty roadmap (no placeholders) when the file is
    missing, so a freshly-bootstrapped environment with no curated
    upcoming list still serves the rest of the catalog.
    """
    target = path or _ROADMAP_PATH
    if not target.exists():
        return Roadmap(placeholders=[])

    mtime = target.stat().st_mtime
    if path is None and _ROADMAP_CACHE and _ROADMAP_CACHE[0][0] == mtime:
        return _ROADMAP_CACHE[0][1]

    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("roadmap.yaml at {} failed to parse: {}", target, exc)
        return Roadmap(placeholders=[])
    if not isinstance(data, dict):
        return Roadmap(placeholders=[])

    # P4.1 audit fix: filter past-dated entries *before* validation so a
    # forgotten placeholder dated for last week doesn't take the whole
    # catalog surface down. Past-dated entries are logged at WARNING so
    # operators see the drop (one log line per dropped id) without the
    # users seeing a broken catalog. The remaining list still flows
    # through the original validator — any *other* malformed entry
    # (missing teaser, bad language) still surfaces.
    today = datetime.now(UTC).date()
    raw_entries = data.get("placeholders")
    if isinstance(raw_entries, list):
        kept: list[Any] = []
        for entry in raw_entries:
            if isinstance(entry, dict):
                raw_date = entry.get("target_release_date")
                parsed_date = _coerce_date(raw_date)
                if parsed_date is not None and parsed_date < today:
                    logger.warning(
                        "roadmap.yaml: dropping past-dated placeholder "
                        "id={} date={}",
                        entry.get("id"),
                        parsed_date.isoformat(),
                    )
                    continue
            kept.append(entry)
        data = {**data, "placeholders": kept}

    try:
        roadmap = Roadmap.model_validate(data)
    except ValidationError as exc:
        # Belt + braces: even after the past-dated filter, a malformed
        # entry (missing required field, bad enum) would historically
        # raise on the catalog endpoint. We log loudly and degrade to
        # an empty roadmap so the rest of the catalog still renders.
        logger.warning(
            "roadmap.yaml at {} failed Pydantic validation: {}", target, exc
        )
        return Roadmap(placeholders=[])

    if path is None:
        _ROADMAP_CACHE.clear()
        _ROADMAP_CACHE.append((mtime, roadmap))
    return roadmap


def filter_collisions(
    roadmap: Roadmap, shipped_mission_ids: Iterable[str]
) -> Roadmap:
    """Drop placeholders whose ``id`` collides with a shipped mission.

    The author-time invariant (placeholder ids must be globally unique
    against the catalogue) is enforced by ``mission.yaml`` review, but a
    drift would otherwise surface as two cards with the same id in the
    catalog — a confusing, hard-to-debug state. We log loudly on
    collision and drop the offending placeholder so the rest of the
    roadmap still renders.

    The collision check is a case-sensitive set membership against the
    supplied ``shipped_mission_ids`` (typically loaded from the DB so
    deploy-time renames are honoured). Returns a *new* ``Roadmap`` —
    the input is not mutated.
    """
    shipped = set(shipped_mission_ids)
    if not shipped:
        return roadmap
    kept: list[RoadmapPlaceholder] = []
    for placeholder in roadmap.placeholders:
        if placeholder.id in shipped:
            logger.warning(
                "roadmap.yaml: dropping placeholder id={} — it collides "
                "with a published mission. Rename the placeholder or "
                "remove it from roadmap.yaml.",
                placeholder.id,
            )
            continue
        kept.append(placeholder)
    if len(kept) == len(roadmap.placeholders):
        return roadmap
    return Roadmap(placeholders=kept)


def _coerce_date(value: Any) -> date | None:
    """Best-effort coerce a YAML scalar into a ``date`` for the pre-filter.

    PyYAML returns a ``datetime.date`` for ``YYYY-MM-DD`` scalars and a
    ``str`` for quoted values; we accept both. Returns ``None`` for
    anything else so the caller can fall through to the Pydantic
    validator (which surfaces a clean error).
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = [
    "Roadmap",
    "RoadmapLanguage",
    "RoadmapPlaceholder",
    "filter_collisions",
    "load_roadmap",
]
