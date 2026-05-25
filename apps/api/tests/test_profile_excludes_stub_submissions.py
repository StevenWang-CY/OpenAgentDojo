"""Grader-failure stubs (``is_stub: True``) must not poison profile
aggregates. A transient sandbox or LLM outage produces a Submission with
all dims at zero; counting it in the radar averages would drag every
user's per-dim score toward zero on every retry. Counting it in
``/me/skills`` would claim the user attempted (and failed) a mission
they never actually attempted.
"""

from __future__ import annotations

import uuid

from app.profiles.router import _aggregate_radar


def _stub_report() -> dict:
    return {
        "total": 0,
        "dimensions": {
            "final_correctness": {"score": 0, "max": 30, "signals": []},
            "verification": {"score": 0, "max": 15, "signals": []},
            "agent_review": {"score": 0, "max": 15, "signals": []},
            "prompt_quality": {"score": 0, "max": 10, "signals": []},
            "context_selection": {"score": 0, "max": 10, "signals": []},
            "safety": {"score": 0, "max": 10, "signals": []},
            "diff_minimality": {"score": 0, "max": 10, "signals": []},
        },
        "is_stub": True,
        "failure_reason": "sandbox provision failed",
    }


def _real_report(scores: dict[str, int]) -> dict:
    return {
        "total": sum(scores.values()),
        "dimensions": {k: {"score": v, "max": 30, "signals": []} for k, v in scores.items()},
    }


def test_aggregate_radar_skips_stub_rows() -> None:
    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()
    reports = [
        (_real_report({"final_correctness": 20}), sid1),
        (_stub_report(), sid2),
    ]
    radar = _aggregate_radar(reports)
    # Only the real report should contribute — the stub's score=0 must NOT
    # drag the mean down.
    assert radar.get("final_correctness") == 20.0


def test_aggregate_radar_handles_pending_dimensions() -> None:
    sid = uuid.uuid4()
    report = {
        "dimensions": {
            "prompt_quality": {"score": None, "max": 10, "signals": []},
            "final_correctness": {"score": 25, "max": 30, "signals": []},
        }
    }
    radar = _aggregate_radar([(report, sid)])
    # Pending (score=None) is unmeasurable, not zero — it must be excluded
    # rather than averaged as zero. The numeric one carries through.
    assert "prompt_quality" not in radar
    assert radar.get("final_correctness") == 25.0
