"""Post-mortem coaching reflection (P1-4 § "Coaching reflection").

The coaching reflection is the only LLM surface in the codebase that
sees user-private text (the scratchpad body). The discipline this
module enforces:

  * **Opt-out is server-authoritative.** If
    ``users.coaching_reflections_enabled`` is False the function
    short-circuits and returns ``None`` *before* touching the LLM. The
    scratchpad never leaves the database.
  * **Cache key contains only hashes.** The notes body is
    SHA-256-hashed and only the hash enters the cache key, so two
    different users with coincidentally identical scratchpads share a
    cache row. (This is the §0.4.6 privacy posture.)
  * **The LLM call goes through the §0.4 chokepoint.** Cache reads /
    writes flow via :func:`app.llm.cache.get_or_generate`; no direct
    ``llm_cache`` writes happen here.
  * **A per-user index row is stamped after every generation** so the
    account-deletion worker can JOIN on it to wipe a user's
    coaching cache rows. The stamp is idempotent (PK on
    ``(user_id, llm_cache_id)``).
  * **Privacy on failure.** On generator failure with no cached row
    we return ``None``. There is no deterministic fallback for
    coaching — the entire value of the feature is the LLM polish, so a
    "degraded" stub is worse than hiding the section.

The function is pure async; the route handler wraps it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.llm import (
    PROMPT_VERSION,
    GeneratedOutput,
    canonical_content_hash,
    get_or_generate,
    render_prompt,
)
from app.missions.cache import cached_manifests
from app.missions.resolver import MissionFolderNotFoundError, resolve_mission_dir
from app.models.coaching_cache_user_index import CoachingCacheUserIndex
from app.models.llm_cache import LLMCache
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User

# The model used for coaching is documented in
# ``apps/api/app/llm/prompts/scratchpad_coaching.md`` frontmatter. Keep
# the literal here in lockstep — bumping the model is a deliberate
# choice that should also bump the prompt revision.
_COACHING_MODEL = "claude-sonnet-4-6"
_COACHING_DOMAIN = "scratchpad_coaching"

# Hard caps on the per-call payload so a runaway scratchpad / 5 000-event
# session can't blow the model's context window or our token budget.
_MAX_NOTES_CHARS = 8 * 1024  # 8 KiB → ~2 000 tokens, well within budget.
_MAX_IDEAL_CHARS = 1500
_MAX_EVENTS = 80
_MAX_EVENT_SUMMARY_CHARS = 240
_RUBRIC_VERSION = "v1"

# Closed-vocabulary supervision event types that are coaching-relevant.
# Mirrors the design's pinned list — kept here (not in domains.py)
# because it's a per-feature filter, not a cache-key concern.
_RELEVANT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "prompt.submitted",
        "agent.responded",
        "patch.applied",
        "command.run",
        "diff.opened",
        "submission.requested",
    }
)

# Inline-anchor regexes. The model is asked to emit ``[event:N]`` and
# ``[note:"<quote>"]`` markers; we parse them out for the route's
# structured response. Best-effort: a missing or malformed marker just
# leaves the metadata as None and the FE renders the prose as-is.
#
# Hard upper bound (10 digits) on the event id capture: keeps a runaway
# model output from triggering pathological regex backtracking and
# bounds the int() conversion below to values that always fit in a
# 64-bit signed integer. Event ids in production are autoincrementing
# bigints — 10 digits covers anything we can realistically grow to.
_EVENT_ANCHOR_RE = re.compile(r"\[event:(\d{1,10})\]")
_NOTE_ANCHOR_RE = re.compile(r'\[note:"([^"\]]{1,160})"\]')


class CoachingReflectionRead(BaseModel):
    """Wire shape returned by :func:`generate_coaching_reflection`.

    All four content fields can be ``None`` simultaneously, which
    encodes "this submission has no coaching reflection to show" — the
    FE silently hides the section. The two metadata fields (cached,
    generated_at) are still populated for observability when the
    function ran but legitimately produced no prose.
    """

    reflection: str | None
    anchored_event_id: int | None
    anchored_note_quote: str | None
    cached: bool
    generated_at: datetime


class CoachingOutcome(StrEnum):
    """Discriminator for :class:`CoachingResult`.

    Replaces the older ``Optional[CoachingReflectionRead]`` signature
    which forced the route to re-query the DB to disambiguate "no
    notes" / "opted out" (both legitimate 200/null) from "LLM down"
    (503) from "internal crash" (500). The route now reads the enum
    and maps to one HTTP shape per outcome.
    """

    OK = "ok"
    OPTED_OUT = "opted_out"
    NO_NOTES = "no_notes"
    LLM_FAILED = "llm_failed"
    INTERNAL_FAILED = "internal_failed"


@dataclass(slots=True)
class CoachingResult:
    """Discriminated result returned by :func:`generate_coaching_reflection`.

    Only the OK outcome carries a payload. The error outcomes carry a
    ``detail`` string for the route's logging path; the FE never sees
    that string — the 503 / 500 envelope uses fixed copy.
    """

    outcome: CoachingOutcome
    payload: CoachingReflectionRead | None = None
    detail: str | None = None


@dataclass(slots=True)
class _LoadedContext:
    """Inputs the coaching call needs, gathered in one round-trip."""

    submission: Submission
    session: SessionRow
    user: User
    notes: SessionNote | None
    events: list[SupervisionEvent]
    manifest_failure_mode: str
    manifest_version: int
    ideal_solution: str
    score_dimensions: dict[str, Any]


async def generate_coaching_reflection(
    db: AsyncSession,
    *,
    submission_id: uuid.UUID,
    settings: Settings,
) -> CoachingResult:
    """Return a discriminated :class:`CoachingResult` for ``submission_id``.

    The five outcomes:

    * ``OK`` — payload carries the reflection prose, parsed anchors,
      cache-hit flag, and generated-at timestamp.
    * ``OPTED_OUT`` — user has ``coaching_reflections_enabled=False``;
      the notes body NEVER leaves the database on this branch.
    * ``NO_NOTES`` — session has no scratchpad row, or body is empty /
      whitespace-only.
    * ``LLM_FAILED`` — the generator raised AND no cache row exists
      (the chokepoint already logged the underlying failure).
    * ``INTERNAL_FAILED`` — anything else (DB blip, manifest load
      crash, …). The route lets FastAPI's default 500 handler render
      these — we never swallow them into a 503 because the FE renders
      503 as "the model is down right now" and a DB outage would
      mislead the user.

    The route maps OK → 200, OPTED_OUT / NO_NOTES → 200 with null body,
    LLM_FAILED → 503, INTERNAL_FAILED → 500 (uncaught).
    """
    try:
        ctx = await _load_context(db, submission_id)
    except Exception as exc:
        # Context loading is mostly SELECTs; a raise here is a DB blip
        # or an unexpected ORM state, NOT an LLM problem. Surface as
        # internal so the route 500s instead of misleading the user
        # with "model unavailable".
        logger.exception(
            "coaching: _load_context crashed (submission_id={}): {}",
            submission_id,
            exc,
        )
        return CoachingResult(
            outcome=CoachingOutcome.INTERNAL_FAILED,
            detail=f"load_context: {type(exc).__name__}",
        )
    if ctx is None:
        # Missing submission / session / user joins. The route already
        # 404s before calling us when the submission isn't graded, so
        # landing here implies a torn join — treat as internal.
        return CoachingResult(
            outcome=CoachingOutcome.INTERNAL_FAILED,
            detail="load_context: no row",
        )

    # Opt-out is server-authoritative. Short-circuit before we touch
    # the LLM or even hash the notes. The notes body never leaves the
    # database on this branch.
    if not ctx.user.coaching_reflections_enabled:
        logger.debug(
            "coaching: user opted out (user_id={}, submission_id={})",
            ctx.user.id,
            submission_id,
        )
        return CoachingResult(outcome=CoachingOutcome.OPTED_OUT)

    notes_body = (ctx.notes.body if ctx.notes is not None else "").strip()
    if not notes_body:
        logger.debug(
            "coaching: no notes (submission_id={})", submission_id
        )
        return CoachingResult(outcome=CoachingOutcome.NO_NOTES)

    filtered_events = [e for e in ctx.events if e.event_type in _RELEVANT_EVENT_TYPES]
    events_timeline = _build_events_timeline(filtered_events, ctx.session.started_at)

    # Canonicalise the cache inputs. Notes + events are hashed, not
    # embedded — the privacy posture is "the cache key is content-
    # addressed but never reconstructable to the user's text".
    inputs = {
        "notes_sha256": hashlib.sha256(notes_body.encode("utf-8")).hexdigest(),
        "events_sha256": hashlib.sha256(
            _canonical_json_bytes(events_timeline)
        ).hexdigest(),
        "mission_id": ctx.session.mission_id,
        "mission_version": ctx.manifest_version,
        "failure_mode": ctx.manifest_failure_mode,
        "score_dimensions_sha256": hashlib.sha256(
            _canonical_json_bytes(ctx.score_dimensions)
        ).hexdigest(),
        "rubric_version": _RUBRIC_VERSION,
    }
    content_hash = canonical_content_hash(inputs)

    async def _generator() -> GeneratedOutput:
        # ``_build_client`` is a module-level factory the tests can
        # monkeypatch to inject a fake SDK without reaching into
        # Bedrock. The production path builds a real AnthropicClient
        # via :mod:`app.agent.llm`.
        client = _build_client()
        system, user_prompt = render_prompt(
            "scratchpad_coaching",
            notes=notes_body,
            events_timeline=events_timeline,
            failure_mode=ctx.manifest_failure_mode or "(unspecified)",
            ideal_solution=ctx.ideal_solution or "(no ideal solution shipped)",
            score_dimensions=ctx.score_dimensions or {},
        )
        resp = await client.messages_create(
            model=_COACHING_MODEL,
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp)
        usage = getattr(resp, "usage", None)
        return GeneratedOutput(
            output=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    # Wrap the chokepoint call in a tight try/except so an LLM-side
    # failure surfaces cleanly as ``LLM_FAILED`` while a non-LLM crash
    # (e.g. a SQLAlchemy fault inside ``_persist`` after the generator
    # already returned) bubbles for the route's 500 handler.
    try:
        cached = await get_or_generate(
            db,
            domain=_COACHING_DOMAIN,
            content_hash=content_hash,
            prompt_version=PROMPT_VERSION,
            model_id=_COACHING_MODEL,
            generator=_generator,
            fallback=None,
        )
    except Exception as exc:
        # The chokepoint re-raises generator failures when fallback is
        # None (coaching has no fallback — see module docstring). The
        # chokepoint has already incremented ``llm_generation_failed_total``;
        # we surface the typed outcome so the route 503s.
        logger.warning(
            "coaching: get_or_generate failed (submission_id={}): {}",
            submission_id,
            exc,
        )
        return CoachingResult(
            outcome=CoachingOutcome.LLM_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )

    # Stamp the per-user link so account deletion / data export can
    # find the row. Idempotent via PK; cache-hit reads are also stamped
    # so a user who triggered the generation indirectly (via a shared
    # hash) is still surfaced to the deletion worker.
    await _stamp_user_index(
        db,
        user_id=ctx.user.id,
        domain=_COACHING_DOMAIN,
        content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
    )

    # Pull the generated_at from the persisted row so two callers see
    # the same wall-clock timestamp (cache-hit semantics).
    row = await _lookup_cache_row(
        db,
        domain=_COACHING_DOMAIN,
        content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
    )
    generated_at = row.generated_at if row is not None else datetime.now(UTC)

    event_id, note_quote = _parse_anchors(cached.output)
    # Validate the parsed event_id against the actual events we loaded
    # — a model hallucination that emits ``[event:9999]`` for a session
    # with three events would otherwise render as a dead anchor on the
    # FE timeline. Drop the anchor in that case so the FE renders the
    # prose without the (broken) inline link.
    if event_id is not None:
        valid_ids = {int(e.id) for e in ctx.events}
        if event_id not in valid_ids:
            logger.debug(
                "coaching: anchor event id {} not in session events; dropping",
                event_id,
            )
            event_id = None

    payload = CoachingReflectionRead(
        reflection=cached.output,
        anchored_event_id=event_id,
        anchored_note_quote=note_quote,
        cached=cached.cache_hit,
        generated_at=generated_at,
    )
    return CoachingResult(outcome=CoachingOutcome.OK, payload=payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client() -> Any:
    """Construct the AnthropicClient used for coaching generation.

    Production callers get the real :class:`app.agent.llm.AnthropicClient`
    pointed at ``claude-sonnet-4-6``. Tests monkeypatch this function
    to return a fake whose ``messages_create`` is fully controlled.
    """
    from app.agent.llm import AnthropicClient

    return AnthropicClient(
        model=_COACHING_MODEL,
        call_timeout_seconds=20.0,
        max_retries=1,
    )


async def _load_context(
    db: AsyncSession, submission_id: uuid.UUID
) -> _LoadedContext | None:
    """Gather submission + session + user + notes + events + mission.

    Returns ``None`` when the submission or its joined rows can't be
    resolved (the route surfaces 404 in that case; we return ``None``
    here so the function stays pure).
    """
    submission = (
        await db.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        return None
    session = (
        await db.execute(select(SessionRow).where(SessionRow.id == submission.session_id))
    ).scalar_one_or_none()
    if session is None:
        return None
    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None:
        return None
    notes = (
        await db.execute(
            select(SessionNote).where(SessionNote.session_id == session.id)
        )
    ).scalar_one_or_none()

    events = list(
        (
            await db.execute(
                select(SupervisionEvent)
                .where(SupervisionEvent.session_id == session.id)
                .order_by(SupervisionEvent.occurred_at, SupervisionEvent.id)
            )
        )
        .scalars()
        .all()
    )

    # Mission manifest — the loader is best-effort. Tutorial missions
    # have no failure mode; we surface an empty string rather than
    # ``None`` so the prompt renders.
    failure_mode = ""
    manifest_version = 1
    ideal_solution = ""
    loaded = cached_manifests().get(session.mission_id)
    if loaded is not None:
        manifest = loaded.manifest
        failure_mode = manifest.failure_mode.title or manifest.failure_mode.id or ""
        manifest_version = int(manifest.version or 1)
        try:
            folder = resolve_mission_dir(_settings_missions_root(), session.mission_id)
            ideal_solution = await _read_ideal_solution_md(folder)
        except (MissionFolderNotFoundError, ValueError, OSError) as exc:
            logger.debug(
                "coaching: ideal_solution unreadable for mission={}: {}",
                session.mission_id,
                exc,
            )

    score_dimensions: dict[str, Any] = {}
    if isinstance(submission.score_report, dict):
        raw = submission.score_report.get("dimensions") or {}
        if isinstance(raw, dict):
            # Project each dimension to its scalar score; the verbose
            # signal list / max columns just bloat the prompt without
            # carrying coaching signal.
            for k, v in raw.items():
                if isinstance(v, dict) and "score" in v:
                    score_dimensions[k] = v.get("score")
                else:
                    score_dimensions[k] = v

    return _LoadedContext(
        submission=submission,
        session=session,
        user=user,
        notes=notes,
        events=events,
        manifest_failure_mode=failure_mode,
        manifest_version=manifest_version,
        ideal_solution=ideal_solution,
        score_dimensions=score_dimensions,
    )


def _settings_missions_root():
    """Return the configured missions root directory.

    Module-private (leading underscore) per the design — callers
    outside this module should depend on :func:`app.config.get_settings`
    directly rather than this thin pass-through.
    """
    return get_settings().missions_root


async def _read_ideal_solution_md(folder) -> str:
    """Async-safe read of ``ideal_solution.md`` from the mission folder.

    The blocking ``Path.exists()`` + ``Path.read_text()`` calls would
    otherwise pin the event loop for the duration of the disk read —
    fine on a local fs, surprising on an NFS / EFS-backed missions
    volume. ``asyncio.to_thread`` runs them on the default executor so
    a slow read doesn't block other concurrent coaching requests.

    Returns the file contents truncated to ``_MAX_IDEAL_CHARS`` (so a
    pathological ideal solution can't blow the prompt budget) or an
    empty string when the file is missing.
    """

    def _read_sync() -> str:
        ideal_path = folder / "ideal_solution.md"
        if not ideal_path.exists():
            return ""
        text: str = ideal_path.read_text(encoding="utf-8")
        return text[:_MAX_IDEAL_CHARS]

    return await asyncio.to_thread(_read_sync)


def _build_events_timeline(
    events: list[SupervisionEvent], session_started_at: datetime
) -> list[dict[str, Any]]:
    """Project events to a stable, prompt-friendly list of dicts.

    Each entry carries the event id (the FE will render this as an
    anchor target), a relative ``offset_seconds`` from session start,
    the event kind, and a short truncated summary extracted from the
    payload. We deliberately strip the verbose payload body — the
    coach should reason from kind + timing, not from raw text.
    """
    out: list[dict[str, Any]] = []
    for e in events[:_MAX_EVENTS]:
        # ``occurred_at`` may be naive in tests where SQLite drops tzinfo.
        try:
            offset = (e.occurred_at - session_started_at).total_seconds()
        except TypeError:
            offset = 0.0
        out.append(
            {
                "id": int(e.id),
                "offset_seconds": int(max(0, offset)),
                "kind": e.event_type,
                "summary": _summarise_payload(e.event_type, e.payload or {}),
            }
        )
    return out


def _summarise_payload(event_type: str, payload: dict[str, Any]) -> str:
    """Return a short, prompt-safe summary of a payload.

    We pull the most coaching-relevant scalar field per event type and
    truncate hard. Payloads are also generally small (< 1 KiB) but the
    coach only needs a hint, not the whole text.
    """
    candidate_fields = (
        "prompt",
        "response",
        "command",
        "path",
        "file_path",
        "summary",
        "description",
        "text",
        "chars",
    )
    for field in candidate_fields:
        val = payload.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()[:_MAX_EVENT_SUMMARY_CHARS]
        if isinstance(val, int):
            return f"{field}={val}"
    # Fallback — surface keys so the model at least knows the shape.
    keys = sorted(payload.keys())[:6]
    return f"keys=[{', '.join(keys)}]" if keys else ""


def _canonical_json_bytes(payload: Any) -> bytes:
    """JSON-canonicalise + UTF-8 encode — same primitive used by
    :mod:`app.llm.hashing` so a nested hash here is bit-stable across
    machines."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _extract_text(resp: Any) -> str:
    """Pull the first text block from an Anthropic SDK response.

    Defensive — different mock shapes (the test harness uses a stubbed
    object that mimics the SDK) and the real SDK both surface
    ``resp.content[0].text``, but we tolerate a plain-string
    ``content`` for older mocks.
    """
    content = getattr(resp, "content", None)
    if isinstance(content, list) and content:
        head = content[0]
        text = getattr(head, "text", None)
        if isinstance(text, str):
            return text
        if isinstance(head, dict):
            dict_text = head.get("text")
            if isinstance(dict_text, str):
                return dict_text
    if isinstance(content, str):
        return content
    raise RuntimeError("coaching: unexpected LLM response shape")


def _parse_anchors(text: str) -> tuple[int | None, str | None]:
    """Extract the first ``[event:N]`` and ``[note:"..."]`` markers."""
    event_id: int | None = None
    note_quote: str | None = None
    m_event = _EVENT_ANCHOR_RE.search(text)
    if m_event is not None:
        try:
            event_id = int(m_event.group(1))
        except (TypeError, ValueError):
            event_id = None
    m_note = _NOTE_ANCHOR_RE.search(text)
    if m_note is not None:
        candidate = m_note.group(1).strip()
        if candidate:
            note_quote = candidate
    return event_id, note_quote


async def _lookup_cache_row(
    db: AsyncSession,
    *,
    domain: str,
    content_hash: str,
    prompt_version: int,
) -> LLMCache | None:
    """SELECT the canonical cache row so we can stamp the index +
    surface its generated_at to the caller."""
    return (
        await db.execute(
            select(LLMCache).where(
                LLMCache.domain == domain,
                LLMCache.content_hash == content_hash,
                LLMCache.prompt_version == prompt_version,
            )
        )
    ).scalar_one_or_none()


async def _stamp_user_index(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    domain: str,
    content_hash: str,
    prompt_version: int,
) -> None:
    """Insert a ``coaching_cache_user_index`` row, idempotent on PK.

    Uses an INSERT…ON CONFLICT DO NOTHING on Postgres and tolerates
    the SQLite (test) path by catching IntegrityError + rolling back.
    """
    cache_row = await _lookup_cache_row(
        db,
        domain=domain,
        content_hash=content_hash,
        prompt_version=prompt_version,
    )
    if cache_row is None:
        # No row to link to — the chokepoint either returned a
        # fallback (we pass None, so this can't happen for coaching)
        # or never persisted. Either way, nothing to stamp.
        return

    # The request-scoped ``get_db`` dependency commits on success at
    # the request boundary — calling ``db.commit()`` here would land
    # partial work from the route's other handlers and break the
    # "one request = one atomic transaction" contract. Use ``flush()``
    # so the row is visible to subsequent SELECTs in this same session
    # without taking commit ownership away from the dependency.
    dialect = _dialect_name(db)
    if dialect == "postgresql":
        stmt = (
            pg_insert(CoachingCacheUserIndex)
            .values(user_id=user_id, llm_cache_id=cache_row.id)
            .on_conflict_do_nothing(
                index_elements=("user_id", "llm_cache_id")
            )
        )
        await db.execute(stmt)
        await db.flush()
    else:
        # SQLite tests path. Check first to keep the session clean —
        # a raised IntegrityError on flush would otherwise poison the
        # transaction for any later commit on the same session
        # (FastAPI's request-scoped session would then ``commit`` on
        # tear-down and crash with PendingRollbackError).
        existing = (
            await db.execute(
                select(CoachingCacheUserIndex).where(
                    CoachingCacheUserIndex.user_id == user_id,
                    CoachingCacheUserIndex.llm_cache_id == cache_row.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        db.add(
            CoachingCacheUserIndex(
                user_id=user_id, llm_cache_id=cache_row.id
            )
        )
        await db.flush()


def _dialect_name(db: AsyncSession) -> str:
    """Best-effort dialect lookup; mirrors :func:`app.sessions.notes._dialect_name`.

    ``db.bind`` can be ``None`` when the session has no engine attached
    (some test harnesses bind lazily), and ``db.get_bind()`` raises in
    that case rather than returning ``None``. Catching the failure and
    defaulting to ``postgresql`` keeps the production path correct while
    letting the tests exercise the SQLite branch.
    """
    try:
        engine = db.get_bind()
    except Exception:
        return "postgresql"
    if engine is None:
        return "postgresql"
    dialect = getattr(engine, "dialect", None)
    if dialect is None:
        return "postgresql"
    name = getattr(dialect, "name", None)
    return str(name) if isinstance(name, str) else "postgresql"


__all__ = [
    "CoachingOutcome",
    "CoachingReflectionRead",
    "CoachingResult",
    "generate_coaching_reflection",
]
