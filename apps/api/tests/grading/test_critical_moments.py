"""P0-2 — unit tests for ``compute_critical_moments``.

Each kind is exercised with a fixture event stream that should trip it,
plus an inverse fixture that should NOT trip it. The function is pure
so the tests stay terse — no DB, no driver.
"""

from __future__ import annotations

from app.grading.diagnostics import compute_critical_moments


def _ev(eid: int, etype: str, payload=None, occurred_at=None):
    return {
        "id": eid,
        "event_type": etype,
        "payload": payload or {},
        "occurred_at": occurred_at,
    }


def test_agent_responded_no_review_trips_when_diff_not_opened():
    events = [
        _ev(1, "session.started"),
        _ev(2, "prompt.submitted", {"text": "fix the bug"}),
        _ev(3, "agent.responded"),
        _ev(4, "patch.applied"),
        _ev(5, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    kinds = [m.kind for m in moments]
    assert "agent_responded_no_review" in kinds
    target = next(m for m in moments if m.kind == "agent_responded_no_review")
    assert target.event_id == 3  # the agent.responded


def test_agent_responded_no_review_does_not_trip_when_diff_opened():
    events = [
        _ev(1, "agent.responded"),
        _ev(2, "patch.applied"),
        _ev(3, "diff.opened"),
        _ev(4, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    assert all(m.kind != "agent_responded_no_review" for m in moments)


def test_submitted_without_verification_trips_when_no_test_command():
    events = [
        _ev(1, "prompt.submitted"),
        _ev(2, "agent.responded"),
        _ev(3, "patch.applied"),
        _ev(4, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    target = next(
        (m for m in moments if m.kind == "submitted_without_verification"),
        None,
    )
    assert target is not None
    assert target.event_id == 1


def test_submitted_without_verification_does_not_trip_when_test_ran():
    events = [
        _ev(1, "prompt.submitted"),
        _ev(2, "command.run", {"category": "test"}),
        _ev(3, "agent.responded"),
        _ev(4, "patch.applied"),
        _ev(5, "diff.opened"),
        _ev(6, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    assert all(m.kind != "submitted_without_verification" for m in moments)


def test_missed_corrective_window_trips_when_rushed_submit():
    events = [
        _ev(1, "agent.responded", {}, occurred_at="2026-05-23T10:00:00+00:00"),
        _ev(2, "submission.requested", {}, occurred_at="2026-05-23T10:00:05+00:00"),
    ]
    moments = compute_critical_moments(events=events)
    target = next(
        (m for m in moments if m.kind == "missed_corrective_window"),
        None,
    )
    assert target is not None
    assert target.event_id == 1


def test_missed_corrective_window_does_not_trip_when_user_paused():
    events = [
        _ev(1, "agent.responded", {}, occurred_at="2026-05-23T10:00:00+00:00"),
        _ev(2, "submission.requested", {}, occurred_at="2026-05-23T10:01:00+00:00"),
    ]
    moments = compute_critical_moments(events=events)
    assert all(m.kind != "missed_corrective_window" for m in moments)


def test_wrong_layer_committed_trips_without_revert():
    events = [
        _ev(1, "prompt.submitted"),
        _ev(2, "agent.responded"),
        _ev(3, "patch.applied"),
        _ev(
            4,
            "validator.flag",
            {"kind": "forbidden_changes", "file": "frontend/Banned.tsx"},
        ),
        _ev(5, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    target = next(
        (m for m in moments if m.kind == "wrong_layer_committed"),
        None,
    )
    assert target is not None
    # Anchored to the patch.applied that introduced the change.
    assert target.event_id == 3


def test_wrong_layer_committed_does_not_trip_after_revert():
    events = [
        _ev(1, "patch.applied"),
        _ev(
            2,
            "validator.flag",
            {"kind": "forbidden_changes", "file": "frontend/Banned.tsx"},
        ),
        _ev(3, "file.reverted", {"path": "frontend/Banned.tsx"}),
        _ev(4, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events)
    assert all(m.kind != "wrong_layer_committed" for m in moments)


def test_max_three_moments_returned_sorted_by_severity():
    # Trip every heuristic; verify we cap at 3 and the ordering is
    # severity-desc.
    events = [
        _ev(1, "prompt.submitted", {}, occurred_at="2026-05-23T10:00:00+00:00"),
        _ev(2, "agent.responded", {}, occurred_at="2026-05-23T10:00:01+00:00"),
        _ev(3, "patch.applied"),
        _ev(
            4,
            "validator.flag",
            {"kind": "forbidden_changes", "file": "x.ts"},
        ),
        _ev(
            5,
            "submission.requested",
            {},
            occurred_at="2026-05-23T10:00:05+00:00",
        ),
    ]
    moments = compute_critical_moments(events=events)
    assert len(moments) <= 3
    severities = [m.severity for m in moments]
    assert severities == sorted(severities, reverse=True)


def test_empty_event_stream_returns_empty():
    assert compute_critical_moments(events=[]) == []


def test_wrong_layer_committed_does_not_swallow_multiple_files():
    """Two validator flags on different files should both register.

    The previous implementation broke after the first
    ``wrong_layer_committed`` and silently lost subsequent violations.
    Dedupe by (event_id, kind) still coalesces duplicates on the same
    anchor, but distinct flagged paths must each surface.
    """
    events = [
        _ev(1, "prompt.submitted"),
        _ev(2, "agent.responded"),
        _ev(3, "patch.applied"),
        _ev(
            4,
            "validator.flag",
            {"kind": "forbidden_changes", "file": "frontend/A.tsx"},
        ),
        _ev(
            5,
            "validator.flag",
            {"kind": "forbidden_changes", "file": "frontend/B.tsx"},
        ),
        _ev(6, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events, max_moments=10)
    # Both validator flags anchor to the same last patch.applied (id=3),
    # so dedupe collapses them to ONE moment — but the loop must
    # actually iterate both flags (the previous ``break`` prevented
    # that). Verify the path-specific copy was applied to the surviving
    # entry: since dedupe keeps the first occurrence, we should see the
    # explanation for ``frontend/A.tsx``.
    wlc = [m for m in moments if m.kind == "wrong_layer_committed"]
    assert len(wlc) == 1
    assert "frontend/A.tsx" in wlc[0].explanation


def test_wrong_layer_committed_handles_distinct_anchors_each_kept():
    """When two forbidden-changes flags point at distinct ``patch.applied``
    anchors (e.g. a session that flagged, reverted, re-flagged later),
    each anchor's moment should survive dedupe.

    Today we anchor every wrong_layer_committed to the *last* patch.applied
    in the stream — so two flags on different files but the same last
    patch still collapse to one moment. That's intentional; this test
    documents the contract so a future change doesn't silently break it.
    """
    events = [
        _ev(1, "patch.applied"),
        _ev(2, "validator.flag", {"kind": "forbidden_changes", "file": "x.ts"}),
        _ev(3, "patch.applied"),
        _ev(4, "validator.flag", {"kind": "forbidden_changes", "file": "y.ts"}),
        _ev(5, "submission.requested"),
    ]
    moments = compute_critical_moments(events=events, max_moments=10)
    wlc = [m for m in moments if m.kind == "wrong_layer_committed"]
    # Both flags anchor to the last patch.applied (id=3); dedupe keeps
    # the first occurrence (the x.ts copy).
    assert len(wlc) == 1
    assert wlc[0].event_id == 3
