"""P1 — VERIFY_SECRET rotation list lets retired secrets keep verifying.

Pins the rotation contract:

  * Signing always uses ``settings.verify_secret`` (the current secret).
  * Verification iterates ``[current, *verify_secret_previous_list]``
    and accepts the first match via ``hmac.compare_digest``.
  * A signature minted under a retired secret still verifies until the
    operator drops it from ``VERIFY_SECRET_PREVIOUS``.
  * A signature minted under a secret no longer in the rotation fails.

These invariants are what let an operator rotate without invalidating
PDFs already in the wild.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.reports.verification import (
    compute_hash,
    compute_signature,
    verify_secret,
    verify_secret_resolve_all,
    verify_signature_with_rotation,
)


def _settings(current: str, previous: list[str]) -> SimpleNamespace:
    """Build a minimal settings stub that quacks like ``app.config.Settings``.

    Only the attributes the rotation helpers read are populated.
    """
    return SimpleNamespace(
        verify_secret=current,
        share_token_secret=None,
        session_secret="dev-fallback-32-chars-min-aaaaaaaaaa",
        verify_secret_previous_list=list(previous),
    )


_ENVELOPE = {"submission_id": "abc-123", "total_score": 88}


def test_signing_always_uses_current_secret() -> None:
    """``verify_secret`` returns the current value regardless of the rotation list."""
    s = _settings(current="current-secret-32-chars-min-aaaaa", previous=["prev"])
    assert verify_secret(s) == "current-secret-32-chars-min-aaaaa"


def test_resolve_all_returns_current_then_previous_in_order() -> None:
    """The resolver returns ``[current, *previous]`` and dedupes."""
    s = _settings(
        current="c", previous=["p1", "p2", "p1"]  # duplicate p1 stripped
    )
    assert verify_secret_resolve_all(s) == ["c", "p1", "p2"]


def test_signature_minted_under_current_verifies() -> None:
    s = _settings(current="current-32-chars-min-aaaaaaaaaaaaa", previous=[])
    h = compute_hash(_ENVELOPE)
    sig = compute_signature(h, "current-32-chars-min-aaaaaaaaaaaaa")
    assert verify_signature_with_rotation(h, sig, s) is True


def test_signature_minted_under_retired_secret_still_verifies() -> None:
    """The whole point of the rotation list: a retired secret keeps verifying."""
    h = compute_hash(_ENVELOPE)
    # PDF was sealed a year ago under "old-secret".
    sealed_signature = compute_signature(h, "old-secret-32-chars-min-aaaaaaaaa")
    # Operator has since rotated the active secret. The old value is
    # now in the previous list.
    s = _settings(
        current="new-current-32-chars-min-aaaaaaaaaaa",
        previous=["old-secret-32-chars-min-aaaaaaaaa"],
    )
    assert verify_signature_with_rotation(h, sealed_signature, s) is True


def test_signature_under_secret_not_in_rotation_fails() -> None:
    """A secret that's been fully retired (not in the rotation list) fails."""
    h = compute_hash(_ENVELOPE)
    sealed_signature = compute_signature(h, "ancient-32-chars-min-aaaaaaaaaaaa")
    s = _settings(
        current="new-current-32-chars-min-aaaaaaaaaaa",
        previous=["older-but-still-active-32-chars-min-aaaa"],
    )
    assert verify_signature_with_rotation(h, sealed_signature, s) is False


def test_empty_persisted_signature_never_matches() -> None:
    """A blank persisted signature must never count as 'verified'."""
    s = _settings(current="c-32-chars-min-aaaaaaaaaaaaaaaaaa", previous=[])
    h = compute_hash(_ENVELOPE)
    assert verify_signature_with_rotation(h, "", s) is False
    assert verify_signature_with_rotation(h, None, s) is False  # type: ignore[arg-type]


def test_rotation_path_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verification must go through ``hmac.compare_digest`` (constant-time)."""
    import hmac as _hmac

    calls: list[tuple[str, str]] = []
    real_compare = _hmac.compare_digest

    def _spy(a, b):
        calls.append((str(a), str(b)))
        return real_compare(a, b)

    monkeypatch.setattr("app.reports.verification.hmac.compare_digest", _spy)

    h = compute_hash(_ENVELOPE)
    sig = compute_signature(h, "spy-32-chars-min-aaaaaaaaaaaaaaaaa")
    s = _settings(
        current="spy-32-chars-min-aaaaaaaaaaaaaaaaa", previous=["one", "two"]
    )
    assert verify_signature_with_rotation(h, sig, s) is True
    assert calls, "compare_digest must be invoked on the rotation path"


def test_settings_field_parses_comma_separated() -> None:
    """The Settings property parses ``VERIFY_SECRET_PREVIOUS`` comma-separated."""
    from app.config import Settings

    # We construct Settings directly with the raw env-style string so
    # the parser is the production code path, not a test stub.
    s = Settings(
        arena_env="development",
        verify_secret_previous="one, two ,three,,four",
    )
    assert s.verify_secret_previous_list == ["one", "two", "three", "four"]
