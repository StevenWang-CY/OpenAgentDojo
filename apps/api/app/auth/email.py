"""Magic-link email dispatch — tries three providers in priority order.

1. Resend (if ``settings.resend_api_key`` is set)
2. SMTP via aiosmtplib using ``settings.smtp_host`` / ``settings.smtp_port``
3. Logger fallback — prints the URL to stderr so devs can click it

Returns ``True`` when *some* backend confirmed delivery. The caller may emit a
warning when False, but per spec the API still returns 204 — we don't want to
confirm or deny email existence to unauthenticated callers.

This module has *no* required dependencies beyond stdlib + ``aiosmtplib``; the
Resend path uses ``httpx`` (already a FastAPI dep).
"""

from __future__ import annotations

import email.mime.multipart
import email.mime.text

from loguru import logger

from app.config import Settings


async def send_magic_link_email(
    to_email: str, magic_url: str, settings: Settings
) -> bool:
    """Dispatch the magic-link email. Returns True when *some* backend succeeded."""
    if settings.resend_api_key:
        sent = await _send_via_resend(to_email, magic_url, settings)
        if sent:
            return True

    sent = await _send_via_smtp(to_email, magic_url, settings)
    if sent:
        return True

    # Dev fallback — print to terminal so a developer can click the link.
    # We treat this as a "delivered" outcome only in development; staging /
    # production should not rely on it.
    logger.info("MAGIC LINK for {}: {}", to_email, magic_url)
    return settings.arena_env == "development"


async def _send_via_resend(to_email: str, magic_url: str, settings: Settings) -> bool:
    """POST to the Resend v1 emails API.  Returns True on success."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — skipping Resend delivery")
        return False

    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": "Your Arena login link",
        "html": _html_body(magic_url),
        "text": _text_body(magic_url),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
            if resp.status_code in (200, 201):
                logger.debug("magic link sent via Resend to {}", to_email)
                return True
            logger.warning(
                "Resend returned {} for {}: {}",
                resp.status_code,
                to_email,
                resp.text[:200],
            )
            return False
    except Exception as exc:
        logger.warning("Resend delivery failed for {}: {}", to_email, exc)
        return False


async def _send_via_smtp(to_email: str, magic_url: str, settings: Settings) -> bool:
    """Send via aiosmtplib to ``settings.smtp_host``. Returns True on success."""
    try:
        import aiosmtplib
    except ImportError:
        logger.debug("aiosmtplib not installed — skipping SMTP delivery")
        return False

    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = "Your Arena login link"
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(email.mime.text.MIMEText(_text_body(magic_url), "plain"))
    msg.attach(email.mime.text.MIMEText(_html_body(magic_url), "html"))

    try:
        kwargs: dict = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
            "start_tls": settings.smtp_start_tls,
            "validate_certs": False,
        }
        if settings.smtp_username:
            kwargs["username"] = settings.smtp_username
        if settings.smtp_password:
            kwargs["password"] = settings.smtp_password
        await aiosmtplib.send(msg, **kwargs)
        logger.debug(
            "magic link sent via SMTP ({}:{}) to {}",
            settings.smtp_host,
            settings.smtp_port,
            to_email,
        )
        return True
    except Exception as exc:
        logger.warning(
            "SMTP delivery failed ({}:{}) for {}: {}",
            settings.smtp_host,
            settings.smtp_port,
            to_email,
            exc,
        )
        return False


def _text_body(magic_url: str) -> str:
    return (
        "Click the link below to log in to Agent Supervisor Arena.\n\n"
        f"{magic_url}\n\n"
        "This link expires in 30 minutes and can only be used once.\n"
        "If you did not request this, you can safely ignore this email."
    )


def _html_body(magic_url: str) -> str:
    safe_url = magic_url.replace("&", "&amp;").replace('"', "&quot;")
    return (
        "<!doctype html><html><body style='font-family:sans-serif;max-width:480px;margin:0 auto'>"
        "<h2>Log in to Agent Supervisor Arena</h2>"
        "<p>Click the button below to sign in. "
        "This link expires in 30&nbsp;minutes and can only be used once.</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:12px 24px;'
        "background:#1a56db;color:#fff;text-decoration:none;"
        'border-radius:6px;font-weight:bold">Sign in to Arena</a></p>'
        "<p style='color:#6b7280;font-size:0.85em'>Or copy and paste this URL:<br>"
        f'<code style="word-break:break-all">{safe_url}</code></p>'
        "<p style='color:#6b7280;font-size:0.85em'>"
        "If you did not request this email you can safely ignore it.</p>"
        "</body></html>"
    )
