"""P1-6 — Supervision-event replay artefact builder.

The replay artefact is the platform's most data-rich export: it
captures the full supervision event stream that produced a graded
score plus the P0-11 verification envelope, the score report, and
the user's final diff. The contract is **byte-determinism**: identical
DB state on identical inputs MUST produce identical signed bytes on
every replay, modulo the (excluded-from-signature) ``exported_at``
timestamp.

See [P1_DESIGN.md §P1-6](../../../P1_DESIGN.md) for the design;
[P0_DESIGN_11_13.md §P0-11](../../../P0_DESIGN_11_13.md) for the
sibling verification primitive this builds on.

Canonicalisation rules (load-bearing)
-------------------------------------
1. JSON keys are sorted ASCII-lexicographic at every nesting level.
2. JSON uses ``separators=(",", ":")``, ``ensure_ascii=False``, no
   trailing whitespace.
3. Events are sorted by ``(occurred_at_iso8601, id ASC)`` where
   ``occurred_at`` is microsecond-precision UTC with a ``Z`` suffix.
4. ``mission_pointer.manifest_sha256`` is the SHA-256 of
   ``canonical_json(manifest.model_dump())`` — NOT of the YAML bytes
   (YAML is not deterministic).
5. ``final_diff`` is read directly from
   ``submissions.final_diff`` (the grader persists the
   ``git diff --no-color --no-ext-diff --no-renames`` output at grade
   time).
6. ``exported_at`` is the only non-deterministic field and is
   excluded from the signature; ``exported_at_omitted_from_signature``
   is stamped ``True`` next to it so a third-party verifier knows the
   exact field set fed into HMAC.

LLM independence
----------------
The replay path makes ZERO LLM calls. Every field is read from the
already-persisted DB row state. This matches P1_DESIGN §0.1's
"determinism on every signed artefact" invariant — the LLM is allowed
to write prose (recommendation diagnosis, coaching reflection) but
never to score, rank, or sign.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mission import Mission
from app.models.repo_pack import RepoPack
from app.models.session import SessionRow
from app.models.session_note import SessionNote
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.reports.verification import build_envelope

# Schema identifier — bumped only when the field set changes. The
# privacy-redaction matrix and the canonicalisation rules above are
# pinned to this number.
REPLAY_SCHEMA_VERSION: int = 1
REPLAY_KIND: str = "openagentdojo.replay.v1"

# Closed safe-list of event types whose ``payload`` is structurally
# PII-free (ids + booleans + counts + small enum strings only). For a
# share-token caller, payloads of these event types are emitted
# verbatim; payloads of every other event type are replaced with
# ``{"redacted": true, "byte_count": N}``.
#
# This is the inverse of the previous "redact only this small list"
# design (P1-6 audit item 14): a new event_type added to
# ``event.schema.json`` is REDACTED BY DEFAULT until an operator
# audits its payload shape and adds it here. That fail-closed posture
# is what prevents the next "we forgot to redact a new event"
# regression.
#
# Notes on inclusion:
#   * ``command.run`` is INTENTIONALLY OMITTED — its ``command``
#     field is a free-form shell string that can contain pasted
#     credentials, repo tokens, or local paths. Always redacted for
#     share-token viewers.
#   * ``prompt.submitted`` and ``agent.responded`` are INTENTIONALLY
#     OMITTED — they carry free-form user/agent text and are the
#     original "prompt-bearing" pair. The downstream API uses
#     :data:`PROMPT_BEARING_EVENT_TYPES` (kept as an alias for
#     back-compat / docs).
#   * ``validator.flag`` is INTENTIONALLY OMITTED — its ``message``
#     and ``evidence`` fields can quote excerpts of user-generated
#     content.
#   * ``submission.failed``, ``session.errored`` are INTENTIONALLY
#     OMITTED — their ``detail`` field carries free-form error text
#     that may surface filesystem paths or environment-leaked
#     identifiers.
#
# Notes on scratchpad events (audit fix):
#   * ``note.edited`` and ``note.viewed_during_prompt`` are
#     INTENTIONALLY OMITTED. Their payloads (``bytes`` / ``lines`` /
#     ``seconds_since`` / ``bytes_at_view``) leak the size and dynamics
#     of the user's private scratchpad — a share-token holder peeking
#     at the timeline could infer "they typed 312 chars across 4
#     edits between minute 6 and minute 9" without ever reading the
#     prose. The scratchpad body itself never leaves the DB for
#     share-token holders; the metadata about it must follow the same
#     rule.
REDACTION_SAFE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.started",
        "session.abandoned",
        "session.gave_up",
        "session.reset",
        "session.provision_failed",
        "context.selected",
        "patch.proposed",
        "patch.applied",
        "patch.failed",
        "diff.opened",
        "diff.hovered",
        "file.edited",
        "file.reverted",
        "test.run",
        "submission.requested",
        "submission.graded",
        "tutorial.step_completed",
        "tutorial.dismissed",
        "tutorial.completed",
        "consent.granted",
        "consent.revoked",
        "tab.blurred",
        "tab.focused",
        "paste.large",
        "focus.lost",
        "proctored.violation",
    }
)


# Backward-compatibility alias. Callers that previously imported
# ``PROMPT_BEARING_EVENT_TYPES`` (privacy_matrix tests, FE timeline
# helpers) keep working. The semantic meaning is now "the canonical
# prompt-bearing pair" — it is NO LONGER the full set of redacted
# types (see ``REDACTION_SAFE_EVENT_TYPES`` above for the inverse).
PROMPT_BEARING_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "prompt.submitted",
        "agent.responded",
    }
)


# ---------------------------------------------------------------------------
# Canonicalisation primitives
# ---------------------------------------------------------------------------


# Decimal places every float is rounded to before serialisation. Six
# digits matches the precision the rubric grader records (per-dimension
# scores are integers; weighted aggregates land in this range) and is
# wide enough that two semantically-equal floats off by IEEE-754 noise
# (``0.1 + 0.2`` → ``0.30000000000000004``) collapse to the same string.
# Tightening this is a canonicalisation rule change and would flip the
# golden fixture — touch with care.
_FLOAT_ROUND_DIGITS: int = 6


def _normalise_for_canonical(value: Any) -> Any:
    """Recursively normalise ``value`` so :func:`canonical_json` is byte-stable.

    Walks the artefact tree and rounds every ``float`` to
    ``_FLOAT_ROUND_DIGITS`` decimal places. Without this pass the
    artefact's signature would drift across machines whenever any
    upstream arithmetic introduces IEEE-754 noise — even
    semantically-identical scores like ``0.1 + 0.2`` vs ``0.3`` would
    produce different bytes. Non-float types pass through untouched.

    Non-finite floats (``nan`` / ``inf`` / ``-inf``) are clamped to
    ``None`` and a Prometheus counter is bumped so dashboards can
    surface upstream numerical drift. Allowing them through would
    either round-trip through Python as the non-JSON ``NaN`` token
    (with ``allow_nan=True``) or raise ``ValueError`` inside
    :func:`json.dumps` (with ``allow_nan=False``) — either way the
    signed bytes would be wrong.

    Note: ``bool`` is a subclass of ``int`` in Python; we explicitly
    keep booleans as booleans (they would otherwise survive ``round``
    unchanged, but matching on ``int`` first avoids any confusion).
    """
    import math

    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _bump_nan_clamp_counter()
            return None
        # ``round`` is well-defined and identical across CPython builds
        # and platforms (it implements IEEE-754 round-half-to-even on
        # the binary float, then we re-bind to a fresh float). The
        # result still serialises through ``json.dumps`` deterministically.
        return round(value, _FLOAT_ROUND_DIGITS)
    if isinstance(value, dict):
        return {k: _normalise_for_canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_for_canonical(v) for v in value]
    return value


# Lazy single-instance counter; defined inline (rather than in
# ``app.observability``) so this module remains self-contained for the
# downstream third-party verifier port.
_NAN_CLAMP_COUNTER: Any = None


def _bump_nan_clamp_counter() -> None:
    """Bump the replay NaN-clamp counter; lazily registers it.

    Same posture as :func:`app.reports.verification._bump_nan_clamp_counter`
    — try/except wraps the metrics path so a missing prometheus_client
    or a double registration never breaks signing.
    """
    global _NAN_CLAMP_COUNTER
    try:
        if _NAN_CLAMP_COUNTER is None:
            from prometheus_client import REGISTRY as _REGISTRY
            from prometheus_client import Counter as _Counter

            # Share the verification module's counter where possible so
            # ``/metrics`` exposes a single series for both surfaces.
            existing = getattr(_REGISTRY, "_names_to_collectors", {}).get(
                "canonical_json_nan_clamped_total"
            )
            if existing is not None:
                _NAN_CLAMP_COUNTER = existing
            else:
                _NAN_CLAMP_COUNTER = _Counter(
                    "canonical_json_nan_clamped_total",
                    "Non-finite float (NaN / Inf) values clamped to null while "
                    "serialising a canonical JSON artefact (verification / replay).",
                )
        _NAN_CLAMP_COUNTER.inc()
    except Exception:
        pass


def canonical_json(payload: Any) -> bytes:
    """Serialise ``payload`` to canonical-form bytes.

    Identical to :func:`app.reports.verification.canonical_json` —
    duplicated here as the public API of the replay module so
    third-party verifiers porting the canonicalisation routine to
    another language only need to read one file.

    Rules in effect:

    * Every nested ``float`` is rounded to ``_FLOAT_ROUND_DIGITS`` (6)
      decimal places via :func:`_normalise_for_canonical` before
      serialisation. This collapses IEEE-754 noise so two machines that
      arrived at the same logical score via different floating-point
      paths still produce identical signed bytes.
    * sorted keys at every nesting level
    * ``separators=(",", ":")`` — no whitespace
    * ``ensure_ascii=False`` — non-ASCII handles round-trip as UTF-8
      bytes, not ``\\uXXXX`` escapes
    * ``default=str`` so stray UUIDs / datetimes that slipped past the
      builder still coerce to a stable string form
    """
    return json.dumps(
        _normalise_for_canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
        # Per audit: ``json.dumps`` defaults to emitting ``NaN`` /
        # ``Infinity`` literals that no other JSON parser accepts.
        # ``_normalise_for_canonical`` clamps non-finite floats to
        # ``None`` upstream of this call; ``allow_nan=False`` is the
        # belt-and-braces guard that fails loud if a future caller
        # bypasses the normaliser.
        allow_nan=False,
    ).encode("utf-8")


def _coerce_event_iso(value: Any) -> str:
    """Format ``occurred_at`` as microsecond-precision UTC ``Z`` suffix.

    Distinct from :func:`app.reports.verification._coerce_iso` which
    intentionally strips microseconds — verification envelopes round
    to seconds, but replay events need microsecond ordering so two
    events emitted in the same wall-clock second sort deterministically.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        ts = ts.astimezone(UTC)
        # ``isoformat`` on a UTC datetime emits ``+00:00``; canonicalise
        # to the shorter ``Z`` suffix to match the design and to keep
        # the bytes identical across Python micro-version drift.
        text = ts.isoformat(timespec="microseconds")
        if text.endswith("+00:00"):
            text = text[: -len("+00:00")] + "Z"
        return text
    if isinstance(value, str):
        return value
    return str(value)


def replay_signature(artefact: dict[str, Any], verify_secret: str) -> str:
    """Return the HMAC-SHA256 (hex) over the artefact's signed bytes.

    The signature is computed over ``canonical_json(artefact_minus)``
    where ``artefact_minus`` is ``artefact`` with both ``exported_at``
    and ``replay_signature`` removed. The design's invariant: the same
    submission re-exported a year later produces the same signature
    even though the ``exported_at`` timestamp differs.
    """
    if not isinstance(verify_secret, str) or not verify_secret:
        # ``RuntimeError`` to match :func:`app.reports.verification.verify_secret`
        # — the rest of the codebase ``except RuntimeError`` on missing
        # secret material, and a divergent ``ValueError`` here would
        # silently bypass those handlers.
        raise RuntimeError("verify secret must be a non-empty string")
    signed = {
        k: v
        for k, v in artefact.items()
        if k not in {"exported_at", "replay_signature"}
    }
    payload = canonical_json(signed)
    return hmac.new(
        verify_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


async def _load_envelope_inputs(
    db: AsyncSession,
    submission_id: uuid.UUID,
) -> tuple[Submission, SessionRow, User | None, Mission | None]:
    submission = (
        await db.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise LookupError(f"submission {submission_id} not found")
    session = (
        await db.execute(select(SessionRow).where(SessionRow.id == submission.session_id))
    ).scalar_one_or_none()
    if session is None:
        raise LookupError(f"session {submission.session_id} not found")
    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    mission_row = (
        await db.execute(select(Mission).where(Mission.id == session.mission_id))
    ).scalar_one_or_none()
    return submission, session, user, mission_row


async def _load_events(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> list[SupervisionEvent]:
    """Return every event for ``session_id`` ordered by ``(occurred_at, id)``.

    The order matches the grader's consumption order and is the same
    order the replay artefact serialises. Sorting in Python lets the
    SQLite test path mimic the Postgres index-only scan without
    relying on the planner.
    """
    rows = (
        (
            await db.execute(
                select(SupervisionEvent)
                .where(SupervisionEvent.session_id == session_id)
                .order_by(SupervisionEvent.occurred_at.asc(), SupervisionEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _load_session_note_body(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> str | None:
    """Return the scratchpad body for ``session_id`` or ``None``.

    Used only on the owner-visible artefact (P1_DESIGN §P1-6 privacy
    matrix: "scratchpad body — owner sees ✔; share-token holder sees
    NOT INCLUDED").
    """
    row = (
        await db.execute(select(SessionNote).where(SessionNote.session_id == session_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    return row.body or ""


def _manifest_pointer(
    *,
    mission_row: Mission | None,
    manifest: Any,
    repo_pack: RepoPack | None,
) -> dict[str, Any]:
    """Build the ``mission_pointer`` sub-object.

    Defensive: every field falls back to a stable string ("" / null)
    rather than raising, because the replay path must survive a
    partially-loaded fixture (e.g. tests that don't seed a repo_pack
    row). The signed bytes are still byte-deterministic — they just
    encode "no manifest at grade time" honestly instead of crashing.
    """
    mission_id = ""
    if mission_row is not None and mission_row.id:
        mission_id = str(mission_row.id)
    elif manifest is not None and getattr(manifest, "id", None):
        mission_id = str(manifest.id)

    version: int = 1
    if manifest is not None:
        raw = getattr(manifest, "version", 1)
        try:
            version = int(raw)
        except (TypeError, ValueError):
            version = 1

    manifest_sha256: str = ""
    if manifest is not None:
        try:
            dumped = manifest.model_dump(mode="json")
        except AttributeError:
            # Older pydantic stubs / fixture doubles — fall back to dict().
            try:
                dumped = manifest.dict()
            except Exception:
                dumped = None
        if dumped is not None:
            manifest_sha256 = hashlib.sha256(canonical_json(dumped)).hexdigest()
    if not manifest_sha256 and mission_row is not None and mission_row.manifest_sha256:
        # Persisted catalog hash is a stable surrogate when the on-disk
        # manifest is no longer reachable (mission deleted between
        # grade-time and replay-time).
        manifest_sha256 = mission_row.manifest_sha256

    repo_pack_id = ""
    if mission_row is not None:
        repo_pack_id = mission_row.repo_pack_id or mission_row.repo_pack or ""
    elif manifest is not None and getattr(manifest, "repo", None) is not None:
        repo_pack_id = getattr(manifest.repo, "pack", "") or ""

    repo_pack_sha = ""
    if repo_pack is not None:
        repo_pack_sha = repo_pack.repo_sha or ""

    return {
        "id": mission_id,
        "manifest_sha256": manifest_sha256,
        "repo_pack_id": repo_pack_id,
        "repo_pack_sha": repo_pack_sha,
        "version": version,
    }


def _serialise_event(event: SupervisionEvent, *, redact_payloads: bool) -> dict[str, Any]:
    """Return the canonical JSON-ready dict for one event row.

    Fail-closed redaction (P1-6 audit item 14): when
    ``redact_payloads`` is True (share-token holders), the payload is
    emitted verbatim ONLY if ``event.event_type`` appears in
    :data:`REDACTION_SAFE_EVENT_TYPES`. Every other event type — known
    or unknown to the running build — has its payload replaced with
    ``{"redacted": true, "byte_count": N}`` where ``N`` is the UTF-8
    byte length of ``canonical_json(original_payload)``. The
    byte-count gives a share-token recipient a sense of "how much text
    was here" without revealing the content.

    Owner views (``redact_payloads=False``) always see the verbatim
    payload, irrespective of event type.
    """
    payload = event.payload if event.payload is not None else {}
    if redact_payloads and event.event_type not in REDACTION_SAFE_EVENT_TYPES:
        try:
            byte_count = len(canonical_json(payload))
        except Exception:
            # ``canonical_json`` should never fail on a JSONB-loaded
            # dict, but if it does we still want the artefact to emit
            # — fall back to a SENTINEL marker (``-1``) so an upstream
            # consumer cannot mistake "we could not measure the
            # payload" for "the original payload was zero bytes".
            # Returning ``0`` here would silently look identical to a
            # share-token holder peeking at an empty event.
            byte_count = -1
            logger.warning(
                "[replay] could not canonicalise payload for event {} type={}",
                event.id,
                event.event_type,
            )
        payload_out: Any = {"byte_count": byte_count, "redacted": True}
    else:
        payload_out = payload
    return {
        "event_type": event.event_type,
        "id": int(event.id),
        "occurred_at": _coerce_event_iso(event.occurred_at),
        "payload": payload_out,
    }


async def build_replay(
    db: AsyncSession,
    submission_id: uuid.UUID,
    *,
    redact_payloads: bool,
    verify_secret_value: str,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the deterministic replay artefact for ``submission_id``.

    Inputs:
        db: AsyncSession bound to the request scope.
        submission_id: target submission. The session is resolved
            via the submission's ``session_id`` FK.
        redact_payloads: True for share-token holders. Replaces every
            prompt-bearing event payload with a byte-count marker and
            omits the scratchpad body.
        verify_secret_value: the resolved ``VERIFY_SECRET`` — passed
            in so the caller can fail fast on a misconfigured server
            (the secret-resolution failure path is identical to the
            /verify endpoint).
        exported_at: optional override (tests / determinism harness).
            When None, the current UTC wall clock is stamped.

    Returns the artefact dict. The dict is JSON-serialisable via
    :func:`canonical_json` — that is the canonical form the
    ``replay.json`` endpoint emits.

    Raises ``LookupError`` when the submission or its session row is
    missing (the router translates to 404).
    """
    submission, session, user, mission_row = await _load_envelope_inputs(db, submission_id)

    # Mission manifest from the cache (same path /verify uses) — the
    # manifest_sha256 we hash into the mission_pointer is derived from
    # the manifest's pydantic dump, NOT from on-disk YAML, because YAML
    # serialisation is not byte-deterministic across libyaml versions.
    from app.missions.cache import cached_manifests

    loaded = cached_manifests().get(session.mission_id)
    manifest = loaded.manifest if loaded is not None else None

    # repo_packs row — used only for the ``repo_pack_sha`` field.
    repo_pack_id = (
        (mission_row.repo_pack_id or mission_row.repo_pack) if mission_row is not None else None
    )
    repo_pack: RepoPack | None = None
    if repo_pack_id:
        repo_pack = (
            await db.execute(select(RepoPack).where(RepoPack.id == repo_pack_id))
        ).scalar_one_or_none()

    envelope = build_envelope(
        submission=submission,
        session=session,
        manifest=manifest,
        user=user,
        mission_row=mission_row,
    )

    # The verification_signature already lives on the submission row —
    # the grader stamped it at grade time. We NEVER recompute it from
    # the live secret here (P0-11 invariant: the signature on the wire
    # is the signature that was minted, not whatever the current secret
    # would produce). The /verify endpoint has a separate
    # rotation-detection path; replay deliberately mirrors the persisted
    # bytes so a recruiter can compare them later.
    envelope_signature = submission.verification_signature or ""

    events = await _load_events(db, session.id)
    serialised_events = [
        _serialise_event(ev, redact_payloads=redact_payloads) for ev in events
    ]

    # ``final_diff`` lives on the submission row directly — the grader
    # captured it from ``git diff --no-color --no-ext-diff --no-renames``
    # between the mission's initial_commit and the session's final
    # tree. Replay is read-only; no sandbox reach-back here.
    #
    # Privacy posture (audit fix): the verbatim diff is owner-only.
    # Share-token holders see ``final_diff: null`` + a sibling
    # ``final_diff_byte_count`` so they still know the magnitude of the
    # change without the source bytes. The previous behaviour leaked
    # the entire patch — including any pasted credentials or local
    # paths the user happened to commit — to anyone who held a share
    # URL. Owners keep verbatim access for the post-mortem walkthrough.
    final_diff_raw: str = submission.final_diff or ""

    mission_pointer = _manifest_pointer(
        mission_row=mission_row,
        manifest=manifest,
        repo_pack=repo_pack,
    )

    score_report: dict[str, Any] = submission.score_report or {}

    if redact_payloads:
        final_diff_field: str | None = None
        final_diff_byte_count: int = len(final_diff_raw.encode("utf-8"))
        final_diff_redacted = True
    else:
        final_diff_field = final_diff_raw
        final_diff_byte_count = len(final_diff_raw.encode("utf-8"))
        final_diff_redacted = False

    artefact: dict[str, Any] = {
        "envelope": envelope,
        "envelope_signature": envelope_signature,
        "events": serialised_events,
        "exported_at": _coerce_event_iso(exported_at or datetime.now(UTC)),
        "exported_at_omitted_from_signature": True,
        "final_diff": final_diff_field,
        "final_diff_byte_count": final_diff_byte_count,
        "final_diff_redacted": final_diff_redacted,
        "kind": REPLAY_KIND,
        "mission_pointer": mission_pointer,
        "schema_version": REPLAY_SCHEMA_VERSION,
        "score_report": score_report,
        "submission_id": str(submission.id),
    }

    # Owner-only scratchpad embedding. Share-token holders do NOT see
    # the body even when one exists; that is the strictest reading of
    # the design's privacy matrix.
    if not redact_payloads:
        note_body = await _load_session_note_body(db, session.id)
        if note_body is not None:
            artefact["scratchpad"] = {"body": note_body}

    # Sign last so the signature pins the entire (non-exported_at) form.
    artefact["replay_signature"] = replay_signature(artefact, verify_secret_value)

    return artefact


__all__ = [
    "PROMPT_BEARING_EVENT_TYPES",
    "REDACTION_SAFE_EVENT_TYPES",
    "REPLAY_KIND",
    "REPLAY_SCHEMA_VERSION",
    "build_replay",
    "canonical_json",
    "replay_signature",
]
