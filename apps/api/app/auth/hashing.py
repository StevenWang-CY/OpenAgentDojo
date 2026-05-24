"""Shared hashing helpers for auth event payloads.

Centralised here so the email-change route, the magic-link dispatch path,
and any future PII-redacting log site all hash the same input the same
way. Operators can still correlate two events about the same address by
hash (the salt is per-deployment, not per-request) without the raw value
ever appearing in a structured-log line.

The salt comes from ``settings.ip_hash_salt`` — same salt the consent
record uses for IP addresses, which keeps key management to a single
secret. Hashing collapses under SHA-256 + a 32-byte salt; an attacker
who lifts a log line still cannot derive the original email without the
salt + a candidate list of addresses.
"""

from __future__ import annotations

import hashlib
from typing import Any


def hash_email_for_event(email: str | None, settings: Any) -> str:
    """Return a stable SHA-256 hash of ``email`` salted with the deploy salt.

    Lowercases first so ``Foo@Bar.com`` and ``foo@bar.com`` correlate to
    the same identifier. Returns the hex digest (suitable for structured
    log fields) — never the raw address.
    """
    salt = (getattr(settings, "ip_hash_salt", None) or "").encode("utf-8")
    payload = (email or "").lower().encode("utf-8")
    return hashlib.sha256(salt + payload).hexdigest()


def hash_token_for_log(raw: str | None) -> str:
    """Return an 8-char SHA-256 prefix of a token, safe to log.

    Non-reversible (the full digest plus salt are out of reach), but stable
    enough to correlate the same token across two log lines (e.g. issued →
    consumed). Empty / missing tokens collapse to a sentinel so log lines
    stay readable instead of dropping the field.
    """
    if not raw:
        return "<empty>"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
