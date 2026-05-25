"""Verification envelope + signing primitives for the credentialing artifact (P0-11).

The public ``/verify/{submission_id}`` page renders this envelope. It is
the smallest set of fields a recruiter (or any third party) needs to
confirm:

  * **what** was graded (mission_id, mission_title, mission_version);
  * **who** it belongs to (handle, display_name);
  * **how** it scored (total_score, effective_max, missed_failure_mode,
    score_cap_reason);
  * **when** it was graded (graded_at);
  * **which rubric** produced the score (rubric_version);
  * **how the score relates to the schema** (schema_version,
    attempt_index).

Everything in this module is intentionally pure — there is no DB
session, no HTTP, no I/O. The grader calls :func:`build_envelope` /
:func:`compute_hash` / :func:`compute_signature` at grade time; the
verify endpoint reads the persisted hash + signature off the
``submissions`` row and re-renders the envelope shape from the same DB
columns. Replaying the same inputs MUST produce the same hash, every
time. The unit tests assert this contract.

Determinism contract
--------------------
``compute_hash`` is a SHA-256 of ``canonical_json(envelope)``. The
canonical form sorts keys, drops whitespace, and uses ``default=str`` so
datetimes / UUIDs serialise to the same bytes across replays. Any field
whose serialisation depends on Python representation (``set``,
custom ``__repr__``) MUST be coerced to a primitive in
:func:`build_envelope` before reaching the canonical form.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

# Envelope-shape version. Bumped only when the field set changes — i.e.
# a new key is added or an old one removed. The actual rubric weights
# are versioned separately under :data:`RUBRIC_VERSION`.
ENVELOPE_SCHEMA_VERSION: int = 1

# The rubric version stamped into the envelope. Today this is "v1" — see
# ADR 0011 (rubric rebalance) which folds the M5 weights and the P0
# tweaks into a single contiguous rubric. Bumping this without bumping
# the schema means existing verify pages and PDFs render with a small
# "scored under rubric v1 (current: v2)" note next to the score.
RUBRIC_VERSION: str = "v1"

# Default max for the public envelope when the score report didn't
# record an effective_max (very old graded rows). 100 is the canonical
# rubric total — see ``app.grading.dimensions.RUBRIC_TOTAL``.
_DEFAULT_EFFECTIVE_MAX: int = 100


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def _coerce_iso(value: Any) -> str | None:
    """Return ISO-8601 UTC string for a datetime, ``None`` if absent.

    Strips microseconds — they are not load-bearing in a verification
    envelope and keeping them would let a server clock with a slightly
    different resolution change the hash. We round to seconds.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # Normalise to UTC; if naive, assume UTC (that is the DB column
        # contract for ``TIMESTAMPTZ`` rows that round-tripped through a
        # driver that lost the tz).
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        coerced: str = value.astimezone(UTC).replace(microsecond=0).isoformat()
        return coerced
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` in Python; reject it
        # explicitly so a stray bool doesn't silently end up where an
        # int is expected (e.g. score columns).
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_handle(user: Any) -> str:
    """Return the handle, falling back to a tombstone form when deleted.

    A deleted user (P0-6) has the handle tombstoned to
    ``deleted-<short>``; the envelope mirrors whatever the DB row holds.
    A None user (shouldn't happen, but the column is nullable in old
    fixtures) gets a stable placeholder so the envelope still hashes.
    """
    if user is None:
        return "unknown"
    handle = getattr(user, "handle", None)
    if not handle:
        # Synthesize from the id so the envelope still verifies and a
        # tombstone-without-handle row doesn't render as empty.
        uid = getattr(user, "id", None)
        if uid is not None:
            return f"deleted-{str(uid)[:8]}"
        return "deleted-unknown"
    return str(handle)


def _resolve_display_name(user: Any) -> str | None:
    if user is None:
        return None
    name = getattr(user, "display_name", None)
    if not name:
        return None
    return str(name)


def _resolve_mission_version(manifest: Any) -> int:
    """Mission version from the loaded manifest, defaulting to 1.

    The manifest pydantic model exposes ``version: int = 1`` so older
    missions that don't declare one still produce a stable value.
    """
    if manifest is None:
        return 1
    raw = getattr(manifest, "version", 1)
    coerced = _coerce_int(raw)
    return coerced if coerced is not None else 1


def _resolve_mission_title(manifest: Any, mission_row: Any) -> str:
    if manifest is not None:
        title = getattr(manifest, "title", None)
        if title:
            return str(title)
    if mission_row is not None:
        title = getattr(mission_row, "title", None)
        if title:
            return str(title)
    return ""


def _resolve_attempt_index(session: Any) -> int:
    """``sessions.attempt_index`` (P0-3) — defaults to 1 for older sessions.

    The column was added in migration 0013 with a NOT NULL + DEFAULT 1
    backfill, but defensively coerce in case a fixture row predates
    that migration.
    """
    if session is None:
        return 1
    raw = getattr(session, "attempt_index", 1)
    coerced = _coerce_int(raw)
    return coerced if coerced is not None and coerced >= 1 else 1


def build_envelope(
    *,
    submission: Any,
    session: Any,
    manifest: Any,
    user: Any,
    mission_row: Any = None,
) -> dict[str, Any]:
    """Return the canonical envelope dict for ``submission``.

    The output is **the** verification surface: it must contain only
    fields a recruiter or third-party verifier needs, and it must hash
    the same on every replay. New fields are additive; bump
    :data:`ENVELOPE_SCHEMA_VERSION` when adding one. Removing a field is
    a breaking change for already-issued PDFs and requires an ADR.
    """
    score_report = getattr(submission, "score_report", None) or {}
    effective_max = score_report.get("effective_max")
    if not isinstance(effective_max, int):
        effective_max = _DEFAULT_EFFECTIVE_MAX

    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "submission_id": _coerce_str(getattr(submission, "id", None)) or "",
        "handle": _resolve_handle(user),
        "display_name": _resolve_display_name(user),
        "mission_id": _coerce_str(getattr(session, "mission_id", None)) or "",
        "mission_title": _resolve_mission_title(manifest, mission_row),
        "mission_version": _resolve_mission_version(manifest),
        "rubric_version": RUBRIC_VERSION,
        "total_score": _coerce_int(getattr(submission, "total_score", 0)) or 0,
        "effective_max": int(effective_max),
        "missed_failure_mode": bool(score_report.get("missed_failure_mode", False)),
        "score_cap_reason": _coerce_str(getattr(submission, "score_cap_reason", None)),
        "proctored": False,  # Reserved for future P0-7 / P0-8 integration.
        "attempt_index": _resolve_attempt_index(session),
        "graded_at": _coerce_iso(getattr(submission, "created_at", None)),
    }


# ---------------------------------------------------------------------------
# Hashing + signing
# ---------------------------------------------------------------------------


def canonical_json(envelope: dict[str, Any]) -> bytes:
    """Serialise ``envelope`` to canonical-form bytes (sorted keys, tight).

    ``default=str`` coerces any stray UUID / datetime that slipped past
    the builder. Separators drop the optional whitespace so the bytes
    are identical to what any other JSON-canon implementation produces.

    ``ensure_ascii=False`` keeps non-ASCII handles (e.g. ``"私"``) as
    raw UTF-8 in the canonical bytes rather than encoding them as the
    ``\\uXXXX`` escape sequence Python defaults to. Without this, the
    canonical form of an envelope containing CJK / accented characters
    would not match any other JSON-canon implementation's output, and
    a downstream verifier porting this primitive to another language
    (Go, Rust, JS) would compute a different SHA-256 against bytes
    that look identical when pretty-printed.
    """
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")


def compute_hash(envelope: dict[str, Any]) -> str:
    """Return the lowercase-hex SHA-256 of the canonical envelope."""
    return hashlib.sha256(canonical_json(envelope)).hexdigest()


def compute_signature(envelope_hash: str, secret: str) -> str:
    """Return the lowercase-hex HMAC-SHA256 of ``envelope_hash``.

    The signature is over the **hash string**, not the envelope bytes.
    That keeps the signature a function of the hash alone — rotating
    the secret produces a fresh signature without re-hashing, which is
    what the rotation script needs (see design §P0-11 Open decisions).
    """
    if not isinstance(envelope_hash, str) or not envelope_hash:
        raise ValueError("envelope_hash must be a non-empty hex string")
    if not isinstance(secret, str) or not secret:
        raise ValueError("verify secret must be non-empty")
    return hmac.new(
        secret.encode("utf-8"),
        envelope_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def verify_secret(settings: Any) -> str:
    """Return the secret used to HMAC the verification hash.

    Prefers ``settings.verify_secret`` so a session-cookie or share-token
    secret rotation cannot accidentally invalidate every issued
    verification signature. Falls back to ``share_token_secret`` and
    then ``session_secret`` so dev environments keep working without
    requiring a brand-new secret in ``.env``. Production settings
    validation in ``app.config`` enforces a dedicated value.
    """
    candidate = getattr(settings, "verify_secret", None)
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    share = getattr(settings, "share_token_secret", None)
    if isinstance(share, str) and share.strip():
        return share
    session_secret = getattr(settings, "session_secret", None)
    if isinstance(session_secret, str) and session_secret.strip():
        return session_secret
    raise RuntimeError(
        "no verify secret resolvable from settings — "
        "set VERIFY_SECRET (or fall back to SHARE_TOKEN_SECRET / "
        "SESSION_SECRET) before grading any submission"
    )


# ---------------------------------------------------------------------------
# Public helpers for callers that just want "hash + signature in one go".
# ---------------------------------------------------------------------------


def stamp(envelope: dict[str, Any], secret: str) -> tuple[str, str]:
    """Convenience: return ``(hash, signature)`` for ``envelope``."""
    h = compute_hash(envelope)
    return h, compute_signature(h, secret)


__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "RUBRIC_VERSION",
    "build_envelope",
    "canonical_json",
    "compute_hash",
    "compute_signature",
    "stamp",
    "verify_secret",
]


# ---------------------------------------------------------------------------
# Internal: a stable UUID helper for envelopes that may be built from a
# Submission instance lacking a server-assigned id (the runner pre-allocates
# the id via ``uuid.uuid4()`` so the envelope can be computed BEFORE
# ``db.flush()`` — kept here so the runner doesn't need to import uuid for
# this one call site).
# ---------------------------------------------------------------------------


def fresh_submission_id() -> uuid.UUID:
    return uuid.uuid4()
