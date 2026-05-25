"""Agent Review dwell-time + meaningful-edit signals (P1-2).

Pre-P1-2 the dimension was gameable by event-puppeteering: opening the
diff and editing/reverting any single character scored 11/15 without any
real review. The new rules:

* diff-opened credit (+6) requires ``dwell >= 5000 ms`` between the
  ``diff.opened`` event and the next event in the session. A sub-second
  open-and-close earns partial credit only (+3).
* meaningful-edit credit (+5) requires ``file.edited`` to have changed
  at least one line OR a ``file.reverted`` event. A no-op write (Monaco
  serialises every save) is no longer credited.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Manifest:
    id: str = "agent-review-dwell"


def _evt(et: str, payload: dict, ts: str) -> dict:
    return {"event_type": et, "payload": payload, "occurred_at": ts}


def _score(events: list[dict]) -> object:
    from app.grading.score import _score_agent_review

    return _score_agent_review(events, _Manifest())


def test_diff_dwell_under_5s_earns_partial_credit() -> None:
    """Sub-5s open-and-close → +3 (not the full +6)."""
    events = [
        _evt("patch.applied", {}, "2026-05-22T10:00:00Z"),
        _evt("diff.opened", {}, "2026-05-22T10:00:10Z"),
        # Two seconds later — the supervisor closed without reading.
        _evt("file.edited", {"path": "src/a.ts", "added": 1, "removed": 0}, "2026-05-22T10:00:12Z"),
        _evt("prompt.submitted", {"text": "let me revise"}, "2026-05-22T10:00:13Z"),
    ]
    ds = _score(events)
    assert any("open-and-close, partial credit only" in s for s in ds.signals)
    # 3 (diff partial) + 5 (meaningful edit) + 4 (revise prompt) = 12.
    assert ds.score == 12


def test_diff_dwell_over_5s_earns_full_credit() -> None:
    events = [
        _evt("patch.applied", {}, "2026-05-22T10:00:00Z"),
        _evt("diff.opened", {}, "2026-05-22T10:00:10Z"),
        # 30s of dwell — supervisor was reading.
        _evt("file.edited", {"path": "src/a.ts", "added": 1, "removed": 1}, "2026-05-22T10:00:40Z"),
        _evt("prompt.submitted", {"text": "let me revise"}, "2026-05-22T10:00:45Z"),
    ]
    ds = _score(events)
    assert any("(dwell 30000 ms)" in s for s in ds.signals)
    assert ds.score == 15


def test_zero_line_edit_not_credited() -> None:
    """The headline P1-2 case: a Monaco save with no actual change must
    not count as a meaningful edit."""
    events = [
        _evt("patch.applied", {}, "2026-05-22T10:00:00Z"),
        _evt("diff.opened", {}, "2026-05-22T10:00:10Z"),
        _evt(
            "file.edited", {"path": "src/a.ts", "added": 0, "removed": 0}, "2026-05-22T10:00:30Z"
        ),  # no-op write
        _evt("prompt.submitted", {"text": "let me revise"}, "2026-05-22T10:00:35Z"),
    ]
    ds = _score(events)
    assert any("file.edited events present but all were no-op" in s for s in ds.signals)
    # +6 diff (dwell ok) + 0 edit + 4 revise = 10.
    assert ds.score == 10


def test_file_reverted_always_meaningful_even_without_line_counts() -> None:
    events = [
        _evt("patch.applied", {}, "2026-05-22T10:00:00Z"),
        _evt("diff.opened", {}, "2026-05-22T10:00:10Z"),
        # 10s dwell, then revert.
        _evt("file.reverted", {"path": "src/a.ts"}, "2026-05-22T10:00:20Z"),
        _evt("prompt.submitted", {"text": "let me revise"}, "2026-05-22T10:00:21Z"),
    ]
    ds = _score(events)
    assert any("meaningful file edit or revert" in s for s in ds.signals)
    assert ds.score == 15


def test_diff_never_opened_after_patch_earns_zero() -> None:
    """The diff-after-patch + dwell gate still subsumes the original
    'open after last patch' requirement."""
    events = [
        _evt("diff.opened", {}, "2026-05-22T10:00:00Z"),  # before patch
        _evt("patch.applied", {}, "2026-05-22T10:00:30Z"),
        _evt("file.edited", {"path": "src/a.ts", "added": 1, "removed": 0}, "2026-05-22T10:01:00Z"),
        _evt("prompt.submitted", {"text": "revise"}, "2026-05-22T10:01:05Z"),
    ]
    ds = _score(events)
    assert any("diff not opened after the most recent patch applied" in s for s in ds.signals)
    # 0 + 5 + 4 = 9.
    assert ds.score == 9
