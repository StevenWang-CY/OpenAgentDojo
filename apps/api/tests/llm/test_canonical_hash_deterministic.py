"""Canonical hashing is order-independent at the top level.

Same payload with shuffled keys must produce the same SHA-256 digest;
otherwise cache keys would drift whenever the caller's dict construction
order changed (which Python does not guarantee across versions).
"""

from __future__ import annotations

from app.llm.hashing import canonical_content_hash


def test_same_payload_with_different_key_order_hashes_identically() -> None:
    a = {
        "weakest_dim": "agent_review",
        "weakest_dim_avg": 8.4,
        "weakest_dim_attempts": 3,
        "recommended_mission_ids": ("m1", "m2", "m3"),
        "rubric_version": "v1",
    }
    b = {
        "rubric_version": "v1",
        "recommended_mission_ids": ("m1", "m2", "m3"),
        "weakest_dim_avg": 8.4,
        "weakest_dim": "agent_review",
        "weakest_dim_attempts": 3,
    }
    assert canonical_content_hash(a) == canonical_content_hash(b)


def test_different_payloads_hash_differently() -> None:
    a = {"weakest_dim": "agent_review", "rubric_version": "v1"}
    b = {"weakest_dim": "prompt_quality", "rubric_version": "v1"}
    assert canonical_content_hash(a) != canonical_content_hash(b)


def test_hash_is_sha256_hex() -> None:
    h = canonical_content_hash({"a": 1})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_array_order_is_meaningful() -> None:
    """Re-ordering an array entry IS a different input — the hash should change."""
    a = {"ids": ["m1", "m2", "m3"]}
    b = {"ids": ["m3", "m2", "m1"]}
    assert canonical_content_hash(a) != canonical_content_hash(b)


def test_unicode_payload_is_stable() -> None:
    """``ensure_ascii=False`` keeps non-ASCII content stable across platforms."""
    a = {"title": "café"}
    b = {"title": "café"}
    assert canonical_content_hash(a) == canonical_content_hash(b)
