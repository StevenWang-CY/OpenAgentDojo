"""``reset_then_repeated_same_mistake`` critical-moment unit tests (P0-12)."""

from __future__ import annotations

from app.grading.diagnostics import compute_critical_moments


def _ev(eid: int, event_type: str, occurred_at: str | None = None, **payload):
    return {
        "id": eid,
        "event_type": event_type,
        "payload": payload,
        "occurred_at": occurred_at,
    }


def test_two_resets_surface_one_moment_anchored_to_second() -> None:
    events = [
        _ev(1, "session.started", "2026-05-25T10:00:00+00:00"),
        _ev(2, "context.selected", "2026-05-25T10:01:00+00:00"),
        _ev(
            3,
            "session.reset",
            "2026-05-25T10:05:00+00:00",
            files_discarded=4,
            had_agent_patch=True,
            seconds_into_session=300,
        ),
        _ev(4, "prompt.submitted", "2026-05-25T10:06:00+00:00"),
        _ev(
            5,
            "session.reset",
            "2026-05-25T10:10:00+00:00",
            files_discarded=2,
            had_agent_patch=True,
            seconds_into_session=600,
        ),
        _ev(6, "submission.requested", "2026-05-25T10:11:00+00:00"),
    ]
    moments = compute_critical_moments(events=events)
    kinds = [m.kind for m in moments]
    assert "reset_then_repeated_same_mistake" in kinds
    moment = next(m for m in moments if m.kind == "reset_then_repeated_same_mistake")
    # Anchored to the SECOND reset, not the first.
    assert moment.event_id == 5
    assert moment.severity == 4
    assert "backtrack" in moment.explanation.lower()


def test_single_reset_does_not_fire_moment() -> None:
    events = [
        _ev(1, "session.started", "2026-05-25T10:00:00+00:00"),
        _ev(
            2,
            "session.reset",
            "2026-05-25T10:05:00+00:00",
            files_discarded=4,
            had_agent_patch=True,
            seconds_into_session=300,
        ),
        _ev(3, "submission.requested", "2026-05-25T10:06:00+00:00"),
    ]
    moments = compute_critical_moments(events=events)
    assert not any(m.kind == "reset_then_repeated_same_mistake" for m in moments)


def test_reset_moment_fires_even_without_submit() -> None:
    """A reset → abandon flow still needs the diagnostic."""
    events = [
        _ev(1, "session.started", "2026-05-25T10:00:00+00:00"),
        _ev(
            2,
            "session.reset",
            "2026-05-25T10:05:00+00:00",
            files_discarded=4,
            had_agent_patch=True,
            seconds_into_session=300,
        ),
        _ev(
            3,
            "session.reset",
            "2026-05-25T10:09:00+00:00",
            files_discarded=2,
            had_agent_patch=True,
            seconds_into_session=540,
        ),
        # NO submission.requested — user abandoned the session.
    ]
    moments = compute_critical_moments(events=events)
    assert any(m.kind == "reset_then_repeated_same_mistake" for m in moments)
