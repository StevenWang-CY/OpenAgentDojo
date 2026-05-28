"""P1 — canonical_json must never emit NaN / Infinity literals.

``json.dumps`` defaults to ``allow_nan=True``, which emits ``NaN`` and
``Infinity`` as JavaScript-specific tokens that no spec-compliant JSON
parser accepts. For a signed credentialing artefact this means a
downstream verifier in Go / Rust / JS would reject our own bytes.

The contract these tests pin:

  * NaN / Inf inputs get clamped to ``null`` via the canonicaliser.
  * ``canonical_json`` does NOT raise on non-finite inputs (it sanitises
    upstream and ``allow_nan=False`` is the belt-and-braces guard).
  * The clamp bumps ``canonical_json_nan_clamped_total`` so dashboards
    can spot the upstream numerical drift.
  * A signature roundtrip survives the clamp.
"""

from __future__ import annotations

import json

import pytest


def test_replay_canonical_json_clamps_nan() -> None:
    from app.reports.replay import canonical_json

    raw = {"dim": float("nan"), "ok": 0.5}
    bytes_out = canonical_json(raw)
    parsed = json.loads(bytes_out.decode("utf-8"))
    # NaN clamped to null.
    assert parsed["dim"] is None
    assert parsed["ok"] == 0.5


def test_replay_canonical_json_clamps_infinity() -> None:
    from app.reports.replay import canonical_json

    raw = {"a": float("inf"), "b": float("-inf"), "c": 1.0}
    parsed = json.loads(canonical_json(raw).decode("utf-8"))
    assert parsed["a"] is None
    assert parsed["b"] is None
    assert parsed["c"] == 1.0


def test_verification_canonical_json_clamps_nan() -> None:
    from app.reports.verification import canonical_json as ver_canonical_json

    raw = {"total_score": float("nan")}
    parsed = json.loads(ver_canonical_json(raw).decode("utf-8"))
    assert parsed["total_score"] is None


def test_canonical_json_signature_does_not_raise_on_nan() -> None:
    """Signing a payload with a NaN must complete (clamp → sign)."""
    from app.reports.replay import canonical_json, replay_signature

    artefact = {"submission_id": "s", "score_report": {"dim": float("nan")}}
    # The signature path used to blow up with ``ValueError`` because
    # ``allow_nan=False`` raises on non-finite values — the clamp upstream
    # avoids that and produces a stable hex digest.
    sig = replay_signature(artefact, verify_secret="rotation-test-secret-32-chars-aaa")
    assert isinstance(sig, str)
    assert len(sig) == 64
    # Also check the canonical bytes parse as valid JSON.
    json.loads(canonical_json(artefact).decode("utf-8"))


def test_nan_clamp_counter_bumps() -> None:
    """The clamp path bumps the ``canonical_json_nan_clamped_total`` counter."""
    from app.reports.replay import canonical_json
    from app.reports.verification import _bump_nan_clamp_counter as _bump_v

    # Force counter creation (the inline modules lazily instantiate it).
    _bump_v()

    # Snapshot the counter's current value via the global registry.
    from prometheus_client import REGISTRY

    def _current_total() -> float:
        for metric in REGISTRY.collect():
            for sample in metric.samples:
                if sample.name == "canonical_json_nan_clamped_total":
                    return float(sample.value)
        return 0.0

    before = _current_total()
    canonical_json({"x": float("nan"), "y": float("inf")})
    after = _current_total()
    # Two non-finite values clamped → counter went up by at least 2.
    assert after - before >= 2


def test_hashing_canonical_content_hash_rejects_nan() -> None:
    """Cache-key path: raising loud is preferable to silent ``null`` coercion.

    A NaN slipping into a cache key would silently bake the bad value
    into every downstream lookup; ``canonical_content_hash`` uses
    ``allow_nan=False`` without an upstream clamp so the call raises
    ``ValueError`` and the caller has to fix it.
    """
    from app.llm.hashing import canonical_content_hash

    with pytest.raises(ValueError):
        canonical_content_hash({"x": float("nan")})
