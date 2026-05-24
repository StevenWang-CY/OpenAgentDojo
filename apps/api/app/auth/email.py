"""Magic-link email dispatch — tries three providers in priority order.

1. Resend (if ``settings.resend_api_key`` is set)
2. SMTP via aiosmtplib using ``settings.smtp_host`` / ``settings.smtp_port``
3. Logger fallback — prints the URL to stderr so devs can click it

Returns ``True`` when *some* backend confirmed delivery. The caller may emit a
warning when False, but per spec the API still returns 204 — we don't want to
confirm or deny email existence to unauthenticated callers.

This module has *no* required dependencies beyond stdlib + ``aiosmtplib``; the
Resend path uses ``httpx`` (already a FastAPI dep).

The same dispatch chain serves the P0-6 email-change flow (``kind='email_change'``)
and the deletion-scheduled confirmation. Each variant supplies its own subject
line, plaintext body, and HTML body — the transport layer is the same.
"""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
from dataclasses import dataclass
from typing import Any

import aiosmtplib
from loguru import logger

from app.auth.hashing import hash_email_for_event
from app.config import Settings


@dataclass(slots=True, frozen=True)
class _EmailPayload:
    """Pre-rendered email content for the dispatch chain.

    Keeps the Resend / SMTP / dev-fallback paths from each having to
    duplicate the subject + body assembly per email kind.
    """

    subject: str
    text: str
    html: str


async def send_magic_link_email(to_email: str, magic_url: str, settings: Settings) -> bool:
    """Dispatch the magic-link email. Returns True when *some* backend succeeded."""
    payload = _EmailPayload(
        subject="Your OpenAgentDojo login link",
        text=_text_body(magic_url),
        html=_html_body(magic_url),
    )
    return await _dispatch(to_email, payload, settings, dev_label="MAGIC LINK")


async def send_email_change_link(to_email: str, magic_url: str, settings: Settings) -> bool:
    """Dispatch the email-change confirmation link to the user's NEW address.

    The token in ``magic_url`` is purpose=``email_change`` and the confirm
    endpoint rejects tokens that arrive at the wrong endpoint, so this email
    leaks no power beyond "consent to attach this address to the account
    that requested the change."
    """
    payload = _EmailPayload(
        subject="Confirm your new OpenAgentDojo email",
        text=_email_change_text(magic_url),
        html=_email_change_html(magic_url),
    )
    return await _dispatch(to_email, payload, settings, dev_label="EMAIL CHANGE LINK")


async def send_deletion_scheduled_email(
    to_email: str,
    *,
    cancel_url: str,
    scheduled_for_iso: str,
    settings: Settings,
) -> bool:
    """Notify the user that their account is now in the 7-day deletion grace.

    The cancel URL is a deep-link into the web app's ``/account`` page; the
    actual cancel POST is initiated from the frontend (so the email body
    cannot itself be weaponised into a CSRF on the cancel endpoint).
    """
    payload = _EmailPayload(
        subject="Your OpenAgentDojo account is scheduled for deletion",
        text=_deletion_text(cancel_url, scheduled_for_iso),
        html=_deletion_html(cancel_url, scheduled_for_iso),
    )
    return await _dispatch(to_email, payload, settings, dev_label="DELETION CANCEL LINK")


async def _dispatch(
    to_email: str,
    payload: _EmailPayload,
    settings: Settings,
    *,
    dev_label: str,
) -> bool:
    """Resend → SMTP → dev-stderr chain. Returns True on first success."""
    if settings.resend_api_key:
        sent = await _send_via_resend(to_email, payload, settings)
        if sent:
            return True

    sent = await _send_via_smtp(to_email, payload, settings)
    if sent:
        return True

    # Dev fallback — print to terminal so a developer can click the link.
    # Outside development we MUST NOT log the URL: tokens in these URLs are
    # single-use bearer credentials and shipping them to a log aggregator
    # would give any log-reader a working session / pending email change /
    # cancel-deletion handle. The dev branch still prints the raw URL
    # because that's the whole point of the fallback (developer clicks the
    # link in their terminal); the redaction filter exempts development by
    # virtue of the regex passes being idempotent + the dev sink being a
    # human's terminal, not a log aggregator.
    if settings.arena_env == "development":
        logger.info("{} for {}: {}", dev_label, to_email, payload.text)
        return True
    logger.error(
        "email delivery failed email_hash={} subject={!r} via all configured providers",
        hash_email_for_event(to_email, settings),
        payload.subject,
    )
    return False


async def _send_via_resend(to_email: str, payload: _EmailPayload, settings: Settings) -> bool:
    """POST to the Resend v1 emails API.  Returns True on success."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — skipping Resend delivery")
        return False

    body = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": payload.subject,
        "html": payload.html,
        "text": payload.text,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=body,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
            if resp.status_code in (200, 201):
                logger.debug(
                    "email sent via Resend email_hash={} subject={!r}",
                    hash_email_for_event(to_email, settings),
                    payload.subject,
                )
                return True
            logger.warning(
                "Resend returned {} email_hash={} body={!r}",
                resp.status_code,
                hash_email_for_event(to_email, settings),
                resp.text[:200],
            )
            return False
    except Exception as exc:
        logger.warning(
            "Resend delivery failed email_hash={} exc={!r}",
            hash_email_for_event(to_email, settings),
            exc,
        )
        return False


async def _send_via_smtp(to_email: str, payload: _EmailPayload, settings: Settings) -> bool:
    """Send via aiosmtplib to ``settings.smtp_host``. Returns True on success.

    ``aiosmtplib`` is a hard dependency (declared in pyproject.toml) — the
    previous ``try/except ImportError: return False`` silently disabled SMTP
    when the package was missing, which masked broken deploys. A missing
    package is a deploy-time error, not a runtime fallback.
    """
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = payload.subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(email.mime.text.MIMEText(payload.text, "plain"))
    msg.attach(email.mime.text.MIMEText(payload.html, "html"))

    try:
        kwargs: dict[str, Any] = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
            "start_tls": settings.smtp_start_tls,
            # Honour the per-environment cert-validation toggle. Dev /
            # MailHog (which uses a self-signed cert) opts out via
            # SMTP_VERIFY_CERTS=false; staging / prod default to True.
            "validate_certs": settings.smtp_verify_certs,
        }
        if settings.smtp_username:
            kwargs["username"] = settings.smtp_username
        if settings.smtp_password:
            kwargs["password"] = settings.smtp_password
        await aiosmtplib.send(msg, **kwargs)
        logger.debug(
            "email sent via SMTP ({}:{}) email_hash={} subject={!r}",
            settings.smtp_host,
            settings.smtp_port,
            hash_email_for_event(to_email, settings),
            payload.subject,
        )
        return True
    except Exception as exc:
        logger.warning(
            "SMTP delivery failed ({}:{}) email_hash={} exc={!r}",
            settings.smtp_host,
            settings.smtp_port,
            hash_email_for_event(to_email, settings),
            exc,
        )
        return False


def _escape(url: str) -> str:
    """Minimal HTML attribute escaping for click-through links."""
    return url.replace("&", "&amp;").replace('"', "&quot;")


def _text_body(magic_url: str) -> str:
    return (
        "Click the link below to log in to OpenAgentDojo.\n\n"
        f"{magic_url}\n\n"
        "This link expires in 30 minutes and can only be used once.\n"
        "If you did not request this, you can safely ignore this email."
    )


def _html_body(magic_url: str) -> str:
    safe_url = _escape(magic_url)
    return (
        "<!doctype html><html><body style='font-family:sans-serif;max-width:480px;margin:0 auto'>"
        "<h2>Log in to OpenAgentDojo</h2>"
        "<p>Click the button below to sign in. "
        "This link expires in 30&nbsp;minutes and can only be used once.</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:12px 24px;'
        "background:#1a56db;color:#fff;text-decoration:none;"
        'border-radius:6px;font-weight:bold">Sign in to OpenAgentDojo</a></p>'
        "<p style='color:#6b7280;font-size:0.85em'>Or copy and paste this URL:<br>"
        f'<code style="word-break:break-all">{safe_url}</code></p>'
        "<p style='color:#6b7280;font-size:0.85em'>"
        "If you did not request this email you can safely ignore it.</p>"
        "</body></html>"
    )


def _email_change_text(magic_url: str) -> str:
    return (
        "We received a request to attach this email address to an "
        "OpenAgentDojo account.\n\n"
        "Click the link below within 30 minutes to confirm the change. If "
        "you did not request this, you can safely ignore this email — the "
        "change will not take effect.\n\n"
        f"{magic_url}\n"
    )


def _email_change_html(magic_url: str) -> str:
    safe_url = _escape(magic_url)
    return (
        "<!doctype html><html><body style='font-family:sans-serif;max-width:480px;margin:0 auto'>"
        "<h2>Confirm your new email</h2>"
        "<p>We received a request to attach this address to an "
        "OpenAgentDojo account. Click the button below within "
        "30&nbsp;minutes to confirm.</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:12px 24px;'
        "background:#1a56db;color:#fff;text-decoration:none;"
        'border-radius:6px;font-weight:bold">Confirm email change</a></p>'
        "<p style='color:#6b7280;font-size:0.85em'>If you did not request "
        "this, ignore the email — the change will not take effect.</p>"
        "</body></html>"
    )


def _deletion_text(cancel_url: str, scheduled_for_iso: str) -> str:
    return (
        "Your OpenAgentDojo account is scheduled for deletion on "
        f"{scheduled_for_iso} (UTC).\n\n"
        "All sessions, submissions, and data exports will be hard-deleted "
        "at that time. If you change your mind, visit the link below to "
        "cancel the deletion at any point before then:\n\n"
        f"{cancel_url}\n\n"
        "After deletion completes the email and handle will be tombstoned "
        "to prevent re-creation.\n"
    )


def _deletion_html(cancel_url: str, scheduled_for_iso: str) -> str:
    safe_url = _escape(cancel_url)
    return (
        "<!doctype html><html><body style='font-family:sans-serif;max-width:480px;margin:0 auto'>"
        "<h2>Account deletion scheduled</h2>"
        "<p>Your OpenAgentDojo account is scheduled for deletion on "
        f"<strong>{scheduled_for_iso}</strong> (UTC). All sessions, "
        "submissions, and data exports will be hard-deleted at that time.</p>"
        "<p>If you change your mind, you can cancel any time before then:</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:12px 24px;'
        "background:#b91c1c;color:#fff;text-decoration:none;"
        'border-radius:6px;font-weight:bold">Cancel deletion</a></p>'
        "<p style='color:#6b7280;font-size:0.85em'>After deletion completes "
        "the email and handle are tombstoned so the account cannot be "
        "re-created with the same identity.</p>"
        "</body></html>"
    )
