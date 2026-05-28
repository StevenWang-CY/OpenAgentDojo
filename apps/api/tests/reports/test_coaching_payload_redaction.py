"""P4.1B remediation — coaching payload allowlist hides command/path leaks.

Before the fix, ``_summarise_payload`` had a flat list of candidate
fields (``prompt``, ``response``, ``command``, ``path``, ``file_path``,
…) that drained from ANY supervision-event payload regardless of event
type. A ``command.run`` event whose payload carried a shell command
with embedded cookies / API keys would therefore see the raw command
string land in the Bedrock prompt verbatim. Same for
``patch.applied`` payloads whose ``path`` / ``file_path`` carried the
user's home directory (often containing their email).

This file proves the contract:

  * ``command.run`` — only the integer ``chars`` length flows into the
    prompt. The verbatim command string is NOT in the summary.
  * ``patch.applied`` — only the model-synthesised ``summary`` flows
    into the prompt. ``path`` / ``file_path`` are NOT in the summary.
  * the suppression bumps the
    ``coaching_payload_redacted_total{event_type=...}`` counter.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.observability import coaching_payload_redacted_total
from app.reports.coaching import _build_events_timeline, _summarise_payload


def _counter_value(event_type: str) -> float:
    return coaching_payload_redacted_total.labels(event_type=event_type)._value.get()  # type: ignore[attr-defined]


class _FakeEvent:
    """Minimal stand-in for a SupervisionEvent row.

    ``_build_events_timeline`` only reads ``id``, ``event_type``,
    ``payload``, and ``occurred_at`` — no DB session needed.
    """

    def __init__(
        self,
        *,
        id_: int,
        event_type: str,
        payload: dict,
        occurred_at: datetime,
    ) -> None:
        self.id = id_
        self.event_type = event_type
        self.payload = payload
        self.occurred_at = occurred_at


def test_command_run_suppresses_command_string() -> None:
    """``command.run`` payload's command string MUST NOT appear in the
    prompt summary even when it contains user-private content."""
    leaky_command = "curl -H 'Cookie: session=SECRET_COOKIE_DO_NOT_LEAK' https://example.com"
    payload = {"command": leaky_command, "chars": len(leaky_command), "exit_code": 0}
    before = _counter_value("command.run")

    summary = _summarise_payload("command.run", payload)
    after = _counter_value("command.run")

    # The verbatim command string MUST NOT be present.
    assert "SECRET_COOKIE_DO_NOT_LEAK" not in summary
    assert "curl" not in summary
    # The allowlist permits ``chars`` only — should land as ``chars=N``.
    assert summary == f"chars={len(leaky_command)}"
    # Counter must have bumped because the payload had non-allowlisted
    # fields (``command``, ``exit_code``).
    assert after - before >= 1.0


def test_patch_applied_suppresses_path_and_file_path() -> None:
    """``patch.applied`` payload's path / file_path MUST NOT appear in
    the prompt summary even when they contain the user's email."""
    payload = {
        "path": "/home/alice@example.com/repo/src/auth.py",
        "file_path": "/home/alice@example.com/repo/src/auth.py",
        "summary": "edit auth.py to enforce cookie expiry",
    }
    before = _counter_value("patch.applied")

    summary = _summarise_payload("patch.applied", payload)
    after = _counter_value("patch.applied")

    assert "alice@example.com" not in summary
    assert "/home/" not in summary
    # The model-synthesised summary IS allowed through.
    assert summary == "edit auth.py to enforce cookie expiry"
    assert after - before >= 1.0


def test_submission_requested_emits_no_payload_content() -> None:
    """``submission.requested`` payload contributes NOTHING beyond the
    event type itself.

    The allowlist for this event is empty; the summary should be the
    empty string regardless of what the payload carried.
    """
    payload = {"reason": "user-initiated", "score_hint": 42}
    before = _counter_value("submission.requested")

    summary = _summarise_payload("submission.requested", payload)
    after = _counter_value("submission.requested")

    assert summary == ""
    assert "user-initiated" not in summary
    # The payload had non-allowlisted fields, so the redaction counter
    # should have bumped.
    assert after - before >= 1.0


def test_prompt_submitted_passes_through_prompt_text() -> None:
    """The allowlist for ``prompt.submitted`` IS permissive for the user's
    own prompt text — that field is exactly what the coach is reasoning
    about. Confirm it flows through (i.e. the redaction matrix is
    per-event-type, not blanket-deny).
    """
    payload = {"prompt": "Please fix the cookie expiry bug.", "chars": 32}
    summary = _summarise_payload("prompt.submitted", payload)
    assert summary == "Please fix the cookie expiry bug."


def test_build_events_timeline_does_not_leak_command_or_path() -> None:
    """End-to-end check: the timeline that flows into the prompt context
    must not contain the verbatim ``command.run.command`` string or the
    ``patch.applied.path`` even when the payloads carry them.
    """
    base = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    events = [
        _FakeEvent(
            id_=1,
            event_type="command.run",
            payload={
                "command": "echo 'COOKIE_TOKEN_LEAK_PROBE' | tee /tmp/leak.txt",
                "chars": 48,
                "exit_code": 0,
            },
            occurred_at=base,
        ),
        _FakeEvent(
            id_=2,
            event_type="patch.applied",
            payload={
                "path": "/home/leaktest@example.com/repo/src/auth.py",
                "file_path": "/home/leaktest@example.com/repo/src/auth.py",
                "summary": "tighten cookie expiry enforcement",
            },
            occurred_at=base,
        ),
        _FakeEvent(
            id_=3,
            event_type="prompt.submitted",
            payload={"prompt": "Please fix the auth bug", "chars": 22},
            occurred_at=base,
        ),
    ]

    timeline = _build_events_timeline(events, session_started_at=base)

    # Render the timeline back to a single string the way the prompt
    # would consume it and search for the sensitive substrings.
    serialised = " ".join(str(entry) for entry in timeline)
    assert "COOKIE_TOKEN_LEAK_PROBE" not in serialised
    assert "leaktest@example.com" not in serialised
    assert "/home/" not in serialised
    # And confirm the legitimate per-event allowlisted fields ARE there
    # so the coach still has something to reason about.
    assert "tighten cookie expiry enforcement" in serialised
    assert "Please fix the auth bug" in serialised
