"""Consent endpoint payloads (P0-5 — GDPR/CCPA/LGPD opt-in tracking).

The wire shape was designed to keep the FE branchless: a missing record for
a kind is represented as a JSON ``null`` (not an absent key, not an empty
object), so the FE can render "no decision yet" with a single ternary.

Three consent kinds:

* ``analytics``  — optional telemetry (PostHog, OTEL traces).
* ``functional`` — essential cookies (auth session, CSRF, consent itself).
                   The FE banner explains this cannot be opted out of; the
                   server still accepts a POST so the audit row exists.
* ``marketing``  — reserved for future expansion; ships unused.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ConsentKind = Literal["analytics", "functional", "marketing"]


class ConsentRecord(BaseModel):
    """Latest persisted consent decision for a single kind."""

    model_config = ConfigDict(from_attributes=True)

    granted: bool
    version: int
    at: datetime


class ConsentState(BaseModel):
    """Per-kind snapshot of the user's most recent decision (or None).

    A field is ``None`` when no row exists yet for that kind. Once the
    user posts at least one decision (granted or revoked), the field
    surfaces the most recent record.
    """

    analytics: ConsentRecord | None = None
    functional: ConsentRecord | None = None
    marketing: ConsentRecord | None = None


class ConsentUpdate(BaseModel):
    """POST body — kind to record and whether the user granted or revoked.

    The server stamps ``version`` from settings (the current policy
    version) and never trusts a client-supplied version field.
    """

    kind: ConsentKind
    granted: bool
