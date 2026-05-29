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

from app.observability import roadmap_past_dated_dropped_total

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


class RoadmapPastDatedError(ValueError):
    """Raised by :func:`load_roadmap` in ``strict=True`` mode when a
    placeholder's ``target_release_date`` is in the past.

    Strict mode is used by the CI gate (``scripts/validate_missions.py``) so
    a forgotten placeholder fails the build instead of silently dropping
    at runtime. Lenient mode (the default) drops the entry with a WARNING
    and bumps :data:`roadmap_past_dated_dropped_total` so operators can
    spot drift in dashboards.
    """


def load_roadmap(  # noqa: PLR0912 — flat per-field validation; splitting would scatter the schema checks
    path: Path | None = None, *, strict: bool = False
) -> Roadmap:
    """Read and validate the roadmap YAML.

    Caches by mtime so the parsed model is reused across requests in
    lenient mode. The loader returns an empty roadmap (no placeholders)
    when the file is missing, so a freshly-bootstrapped environment with
    no curated upcoming list still serves the rest of the catalog.

    Modes:

    * ``strict=False`` (default; used by the API runtime) — past-dated
      placeholders are dropped with a WARNING and the
      ``roadmap_past_dated_dropped_total{mission_id=...}`` counter is
      bumped per dropped entry. A pre-filter collapse (the kept-list
      was previously non-empty and is now empty) is logged at WARNING
      so the dashboards highlight a roadmap that has gone fully stale.
    * ``strict=True`` (used by CI in ``scripts/validate_missions.py``)
      — past-dated placeholders raise :class:`RoadmapPastDatedError`,
      failing the gate. The strict path also disables the mtime cache
      so successive CI invocations always re-parse against the current
      clock (the lenient cache is intentionally process-local and
      mtime-keyed; calling strict mode does not poison the cache).
    """
    target = path or _ROADMAP_PATH
    if not target.exists():
        return Roadmap(placeholders=[])

    mtime = target.stat().st_mtime
    # Strict mode bypasses the cache — the date check is wall-clock-
    # dependent, and a cached pass from yesterday could mask a date that
    # just slipped past midnight in CI's TZ.
    if not strict and path is None and _ROADMAP_CACHE and _ROADMAP_CACHE[0][0] == mtime:
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
    raw_count = len(raw_entries) if isinstance(raw_entries, list) else 0
    if isinstance(raw_entries, list):
        kept: list[Any] = []
        for entry in raw_entries:
            if isinstance(entry, dict):
                raw_date = entry.get("target_release_date")
                parsed_date = _coerce_date(raw_date)
                if parsed_date is not None and parsed_date < today:
                    entry_id = entry.get("id")
                    entry_id_label = entry_id if isinstance(entry_id, str) else "<unknown>"
                    if strict:
                        raise RoadmapPastDatedError(
                            f"roadmap.yaml: past-dated placeholder id={entry_id_label!r} "
                            f"date={parsed_date.isoformat()} — refresh or remove."
                        )
                    logger.warning(
                        "roadmap.yaml: dropping past-dated placeholder id={} date={}",
                        entry_id_label,
                        parsed_date.isoformat(),
                    )
                    roadmap_past_dated_dropped_total.labels(mission_id=entry_id_label).inc()
                    continue
            kept.append(entry)
        # Detect a *full* collapse from past-dated drops: the source list
        # had entries before, the kept list is now empty. This is the
        # "roadmap silently became empty" failure mode the audit flagged
        # and is louder than the per-entry WARNING.
        if raw_count > 0 and not kept:
            logger.warning(
                "roadmap.yaml: roadmap collapsed to empty after past-dated "
                "filter ({} entries dropped); the upcoming section will "
                "render blank until roadmap.yaml is refreshed.",
                raw_count,
            )
        data = {**data, "placeholders": kept}

    try:
        roadmap = Roadmap.model_validate(data)
    except ValidationError as exc:
        # Belt + braces: even after the past-dated filter, a malformed
        # entry (missing required field, bad enum) would historically
        # raise on the catalog endpoint. We log loudly and degrade to
        # an empty roadmap so the rest of the catalog still renders.
        if strict:
            raise
        logger.warning("roadmap.yaml at {} failed Pydantic validation: {}", target, exc)
        return Roadmap(placeholders=[])

    if path is None and not strict:
        _ROADMAP_CACHE.clear()
        _ROADMAP_CACHE.append((mtime, roadmap))
    return roadmap


def filter_collisions(roadmap: Roadmap, shipped_mission_ids: Iterable[str]) -> Roadmap:
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
    "RoadmapPastDatedError",
    "RoadmapPlaceholder",
    "filter_collisions",
    "load_roadmap",
]
