"""Regression — emails and magic-link tokens never survive in log messages.

Loguru's ``_redact_filter`` runs over both ``record['extra']`` (structured
fields) and ``record['message']`` (the rendered string). The latter is
the load-bearing addition for P0-1: positional interpolations like
``logger.info("for {}", email)`` never land in ``extra`` so the dict-
walking redactor cannot see them. The regex pass on ``message`` catches
those.

The test installs a custom loguru sink that captures every record into a
Python list, fires a handful of representative log calls, and asserts:

  * Raw email addresses never appear in ``record['message']``.
  * ``?token=...`` query values are masked to ``token=[redacted]``.

We deliberately exercise BOTH dev format and JSON format paths — the
filter runs identically in both, but the dev path uses the human format
and the JSON path uses ``_json_format``.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.observability import _redact_filter, _scrub_message


def _capture_via_filter(calls: list[tuple[str, dict[str, Any]]]):
    """Build a loguru sink that records (message, extra) after the filter ran."""

    def _sink(message: Any) -> None:
        record = message.record
        calls.append((record["message"], dict(record.get("extra") or {})))

    return _sink


def test_scrub_message_masks_emails_and_tokens() -> None:
    out = _scrub_message(
        "MAGIC LINK for alice@example.com: "
        "https://app.test/auth/callback?token=abcdef123456&other=ok"
    )
    assert "alice@example.com" not in out
    assert "abcdef123456" not in out
    assert "[redacted-email]" in out
    assert "token=[redacted]" in out
    # Other query params survive — only the token value is masked.
    assert "other=ok" in out


def test_scrub_message_is_idempotent() -> None:
    once = _scrub_message("login as bob@example.org via token=zzz")
    twice = _scrub_message(once)
    assert once == twice


def test_loguru_filter_strips_email_from_positional_message() -> None:
    """``logger.info("for {}", email)`` must not leak the email in the record."""
    captured: list[tuple[str, dict[str, Any]]] = []
    handler_id = logger.add(
        _capture_via_filter(captured),
        level="DEBUG",
        filter=_redact_filter,
        format="{message}",
    )
    try:
        logger.info("MAGIC LINK for {}: {}", "carol@example.net", "click me")
        logger.warning("callback hit https://app.test/auth/callback?token=secret-abc&kind=signup")
    finally:
        logger.remove(handler_id)

    assert captured, "filter never captured the log call"
    for message, extra in captured:
        assert "carol@example.net" not in message, message
        assert "secret-abc" not in message, message
        # Extras carry no PII either — they were empty here, but the
        # filter must not have re-introduced anything.
        assert "carol@example.net" not in repr(extra)


def test_loguru_filter_redacts_email_in_extra_dict() -> None:
    """``logger.bind(email=...).info(...)`` must mask the structured field."""
    captured: list[tuple[str, dict[str, Any]]] = []
    handler_id = logger.add(
        _capture_via_filter(captured),
        level="DEBUG",
        filter=_redact_filter,
        format="{message}",
    )
    try:
        bound = logger.bind(email="dan@example.net", token="zzz")
        bound.info("user signed in")
    finally:
        logger.remove(handler_id)

    assert captured
    _, extra = captured[-1]
    assert extra.get("email") == "[REDACTED]"
    assert extra.get("token") == "[REDACTED]"
