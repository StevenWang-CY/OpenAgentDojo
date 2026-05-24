"""Gap 4 — the email-change magic link points at the real FE route.

The earlier URL template was ``{base_url}/account/email/confirm?token=…``
but the actual Next.js route is ``/auth/email-confirm``. Users clicking
the link from their inbox hit a 404 and the email-change flow stalled
silently with ``pending_email`` set forever.

This test asserts the contract by string-matching the URL returned by
``create_email_change_link`` against the live FE route. Keeping the
assertion as a substring (rather than a full equality) means innocuous
template tweaks (query-string ordering, anchor fragments) don't break the
test, but a path-segment regression DOES.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.magic_link import create_email_change_link
from app.models.user import User


@pytest.mark.asyncio
async def test_email_change_link_targets_frontend_email_confirm_route(
    db_session,
) -> None:
    user = User(
        id=uuid.uuid4(),
        email=f"link-{uuid.uuid4().hex[:8]}@example.com",
        handle=f"lk{uuid.uuid4().hex[:8]}",
    )
    db_session.add(user)
    await db_session.flush()

    url = await create_email_change_link(
        db_session,
        user=user,
        new_email="target@example.com",
        base_url="https://app.openagentdojo.test",
    )

    # The FE route lives at /auth/email-confirm (see
    # apps/web/app/auth/email-confirm/page.tsx). A regression to
    # /account/email/confirm or anything else lands the user on a 404.
    assert "/auth/email-confirm?token=" in url, (
        f"email-change URL must target the FE /auth/email-confirm route; got {url}"
    )
    # Sanity: no double slash, base_url respected.
    assert url.startswith("https://app.openagentdojo.test/auth/email-confirm?token=")
    # Token portion is a non-empty URL-safe string.
    token_part = url.rsplit("token=", 1)[-1]
    assert token_part and " " not in token_part


@pytest.mark.asyncio
async def test_email_change_link_strips_trailing_slash_on_base_url(
    db_session,
) -> None:
    """A trailing slash on base_url MUST NOT produce a doubled slash in the URL."""
    user = User(
        id=uuid.uuid4(),
        email=f"slash-{uuid.uuid4().hex[:8]}@example.com",
        handle=f"sl{uuid.uuid4().hex[:8]}",
    )
    db_session.add(user)
    await db_session.flush()

    url = await create_email_change_link(
        db_session,
        user=user,
        new_email="target2@example.com",
        base_url="https://app.openagentdojo.test/",
    )
    assert "//auth/email-confirm" not in url
    assert "/auth/email-confirm?token=" in url
