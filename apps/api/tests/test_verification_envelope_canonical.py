"""Unit tests for the verification envelope (P0-11).

The envelope is the load-bearing primitive — the verify page renders it,
the PDF embeds the hash, and recruiters rely on the same bytes producing
the same hash on every replay. These tests guard:

  * **Determinism** — same inputs ⇒ same hash, across re-derivation and
    field-order shuffling.
  * **Shape** — every documented field is present.
  * **Coercion** — datetimes round-trip via ISO-8601 with seconds
    resolution; nullable fields render as JSON ``null``.
  * **HMAC chain** — ``compute_signature`` is a function of the hash
    string alone, not the envelope bytes, so secret rotation is cheap.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.reports.verification import (
    ENVELOPE_SCHEMA_VERSION,
    RUBRIC_VERSION,
    build_envelope,
    canonical_json,
    compute_hash,
    compute_signature,
    stamp,
    verify_secret,
)

# ---------------------------------------------------------------------------
# Fixtures — tiny stand-ins for the ORM rows. The envelope builder only
# touches attribute access, so SimpleNamespace is the cheapest fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def submission() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("7c4123ab-0000-0000-0000-000000000001"),
        total_score=78,
        score_cap_reason=None,
        score_report={"effective_max": 100, "missed_failure_mode": False},
        created_at=datetime(2026, 5, 23, 18, 42, 11, 123456, tzinfo=UTC),
    )


@pytest.fixture
def session() -> SimpleNamespace:
    return SimpleNamespace(
        mission_id="auth-cookie-expiration",
        attempt_index=2,
    )


@pytest.fixture
def manifest() -> SimpleNamespace:
    return SimpleNamespace(
        id="auth-cookie-expiration",
        title="Expired Session Cookie Still Grants Access",
        version=1,
    )


@pytest.fixture
def user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        handle="jane",
        display_name="Jane Doe",
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_envelope_keys_match_design(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    expected = {
        "schema_version",
        "submission_id",
        "handle",
        "display_name",
        "mission_id",
        "mission_title",
        "mission_version",
        "rubric_version",
        "total_score",
        "effective_max",
        "missed_failure_mode",
        "score_cap_reason",
        "proctored",
        "attempt_index",
        "graded_at",
    }
    assert set(env.keys()) == expected
    assert env["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert env["rubric_version"] == RUBRIC_VERSION


def test_envelope_hash_is_stable_across_calls(submission, session, manifest, user):
    one = compute_hash(
        build_envelope(submission=submission, session=session, manifest=manifest, user=user)
    )
    two = compute_hash(
        build_envelope(submission=submission, session=session, manifest=manifest, user=user)
    )
    assert one == two
    # Sanity: hash is hex sha256 (64 chars).
    assert len(one) == 64
    int(one, 16)  # raises if not hex


def test_canonical_json_is_key_sorted(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    serialized = canonical_json(env).decode("utf-8")
    parsed = json.loads(serialized)
    assert list(parsed.keys()) == sorted(parsed.keys())
    # No whitespace separators (the canonical form must be tight).
    assert ", " not in serialized
    assert ": " not in serialized


def test_envelope_hash_ignores_python_dict_insertion_order(
    submission, session, manifest, user
):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    # Shuffle by serialising then reordering keys; hash should remain.
    reordered = {k: env[k] for k in reversed(list(env.keys()))}
    assert compute_hash(env) == compute_hash(reordered)


def test_envelope_strips_microseconds(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    # The fixture's created_at has microseconds; the envelope rounds them.
    assert env["graded_at"] == "2026-05-23T18:42:11+00:00"


# ---------------------------------------------------------------------------
# Edge cases — deleted users, tutorial submissions, score cap.
# ---------------------------------------------------------------------------


def test_envelope_handles_tombstoned_user(submission, session, manifest):
    deleted = SimpleNamespace(
        id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        handle="deleted-11111111",
        display_name=None,
    )
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=deleted
    )
    assert env["handle"].startswith("deleted-")
    assert env["display_name"] is None


def test_envelope_handles_score_cap_reason(submission, session, manifest, user):
    submission.score_cap_reason = "gave_up"
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    assert env["score_cap_reason"] == "gave_up"


def test_envelope_defaults_effective_max_when_missing(submission, session, manifest, user):
    submission.score_report = {}
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    assert env["effective_max"] == 100


def test_envelope_defaults_mission_version_to_1(submission, session, user):
    headless_manifest = SimpleNamespace(id="x", title="t")  # no .version
    env = build_envelope(
        submission=submission, session=session, manifest=headless_manifest, user=user
    )
    assert env["mission_version"] == 1


# ---------------------------------------------------------------------------
# HMAC chain
# ---------------------------------------------------------------------------


def test_compute_signature_is_hex_64(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    sig = compute_signature(compute_hash(env), "test-secret-1234567890abcdefghij")
    assert len(sig) == 64
    int(sig, 16)


def test_signature_changes_when_secret_changes(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    h = compute_hash(env)
    sig_a = compute_signature(h, "secret-a-1234567890abcdefghij")
    sig_b = compute_signature(h, "secret-b-1234567890abcdefghij")
    assert sig_a != sig_b


def test_signature_is_function_of_hash_string(submission, session, manifest, user):
    """Rotating the secret produces a fresh signature without rehashing.

    This is what the secret-rotation script relies on (design §P0-11
    Open decisions).
    """
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    h1 = compute_hash(env)
    # Re-derive the envelope and confirm the same hash → same signature
    # for the same secret.
    h2 = compute_hash(
        build_envelope(submission=submission, session=session, manifest=manifest, user=user)
    )
    secret = "test-secret-1234567890abcdefghij"
    assert h1 == h2
    assert compute_signature(h1, secret) == compute_signature(h2, secret)


def test_stamp_returns_matching_hash_and_signature(submission, session, manifest, user):
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=user
    )
    secret = "test-secret-1234567890abcdefghij"
    h, sig = stamp(env, secret)
    assert h == compute_hash(env)
    assert sig == compute_signature(h, secret)


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def test_verify_secret_prefers_dedicated_value():
    settings = SimpleNamespace(
        verify_secret="dedicated",
        share_token_secret="share",
        session_secret="session",
    )
    assert verify_secret(settings) == "dedicated"


def test_verify_secret_falls_back_to_share_then_session():
    settings = SimpleNamespace(
        verify_secret=None, share_token_secret="share", session_secret="session"
    )
    assert verify_secret(settings) == "share"

    settings = SimpleNamespace(
        verify_secret=None, share_token_secret=None, session_secret="session"
    )
    assert verify_secret(settings) == "session"


def test_verify_secret_raises_when_no_secret_resolvable():
    settings = SimpleNamespace(
        verify_secret=None, share_token_secret=None, session_secret=None
    )
    with pytest.raises(RuntimeError):
        verify_secret(settings)


# ---------------------------------------------------------------------------
# Cross-language determinism — canonical_json must emit raw UTF-8 so a
# verifier implemented in Go / Rust / JS computes the same SHA-256 over the
# same bytes a Python implementation produces.
# ---------------------------------------------------------------------------


def test_canonical_json_preserves_raw_utf8_for_non_ascii(
    submission, session, manifest
):
    """Non-ASCII handles (e.g. CJK) must NOT escape to ``\\uXXXX``.

    Without ``ensure_ascii=False`` the canonical bytes would carry
    ``"\\u79c1"`` instead of the raw three UTF-8 bytes for ``"私"``,
    which means any verifier that re-serialises envelope JSON in a
    language whose default is ``ensure_ascii=False`` (Go's
    ``encoding/json``, JS's ``JSON.stringify``, etc.) computes a
    DIFFERENT SHA-256. The credential then fails verification on every
    non-Python implementation.
    """
    cjk_user = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        handle="私",  # "私"
        display_name="私さん",  # "私さん"
    )
    env = build_envelope(
        submission=submission, session=session, manifest=manifest, user=cjk_user
    )
    raw = canonical_json(env)
    # The raw UTF-8 encoding of "私" is the three bytes 0xE7 0xA7 0x81.
    assert "私".encode() in raw, (
        "canonical_json must emit raw UTF-8, not \\uXXXX escape sequences"
    )
    # And the escape form must NOT be present.
    assert b"\\u79c1" not in raw

    # Determinism: a second call against the same envelope returns the
    # same hash. Same input → same hash, every time.
    h1 = compute_hash(env)
    h2 = compute_hash(env)
    assert h1 == h2
    assert len(h1) == 64
