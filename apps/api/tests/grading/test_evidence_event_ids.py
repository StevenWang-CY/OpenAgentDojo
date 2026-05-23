"""P0-2 regression — evidence_event_ids are sorted + deduplicated.

Two replays of the same event stream must produce byte-identical
``score_report`` JSONB (replay determinism, ADR 0006).
"""

from __future__ import annotations

from app.grading.score import DimensionScore, _attach_evidence


def _ev(eid: int, etype: str, payload=None):
    return {
        "id": eid,
        "event_type": etype,
        "payload": payload or {},
        "occurred_at": "2026-05-23T10:00:00+00:00",
    }


def test_attach_evidence_dedupes_and_sorts() -> None:
    # Same id surfaces twice (legitimate: a single event can match more
    # than one heuristic across reruns / reconciliation). The output set
    # must collapse to one id and be sorted ascending.
    dims = {
        "verification": DimensionScore(score=10, max_score=15),
    }
    events = [
        _ev(7, "command.run", {"category": "test"}),
        _ev(3, "command.run", {"category": "typecheck"}),
        _ev(3, "command.run", {"category": "test"}),  # dup id
        _ev(11, "command.run", {"category": "lint"}),
        _ev(99, "command.run", {"category": "install"}),  # filtered out
        _ev(5, "patch.applied"),  # wrong type for verification
    ]
    _attach_evidence(dims, events)
    assert dims["verification"].evidence_event_ids == [3, 7, 11]


def test_attach_evidence_stable_across_event_order() -> None:
    # Same set of ids, different insertion order — output must be identical.
    dims_a = {"agent_review": DimensionScore(score=12, max_score=15)}
    dims_b = {"agent_review": DimensionScore(score=12, max_score=15)}
    events_a = [
        _ev(10, "diff.opened"),
        _ev(2, "diff.opened"),
        _ev(5, "diff.opened"),
    ]
    events_b = list(reversed(events_a))
    _attach_evidence(dims_a, events_a)
    _attach_evidence(dims_b, events_b)
    assert (
        dims_a["agent_review"].evidence_event_ids
        == dims_b["agent_review"].evidence_event_ids
        == [2, 5, 10]
    )


def test_attach_evidence_filters_verification_by_category() -> None:
    dims = {"verification": DimensionScore(score=10, max_score=15)}
    events = [
        _ev(1, "command.run", {"category": "install"}),
        _ev(2, "command.run", {"category": "build"}),
        _ev(3, "command.run", {"category": "test"}),
    ]
    _attach_evidence(dims, events)
    assert dims["verification"].evidence_event_ids == [3]
