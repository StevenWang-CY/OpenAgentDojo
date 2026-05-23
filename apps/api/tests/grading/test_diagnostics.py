"""Per-dimension diagnostic narrative (P2-1).

The narrative reads each dimension's raw signals and synthesises an
actionable cause + next-mission recommendation. These tests pin the
mapping from signal pattern → diagnostic text so a future refactor of
the scoring signals can't silently degrade the user-facing feedback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.grading.diagnostics import (
    RECOMMENDATION_VERSION,
    Diagnostic,
    build_feedback_narrative,
)


@dataclass
class _DS:
    score: int
    max_score: int
    signals: list[str] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.score < 0


def test_rushed_submit_produces_pointed_agent_review_diagnostic() -> None:
    dims = {
        "agent_review": _DS(
            score=0,
            max_score=15,
            signals=["0/15: submit within 15s of agent.responded (hard zero)"],
        )
    }
    narr = build_feedback_narrative(dimensions=dims)
    assert len(narr) == 1
    d = narr[0]
    assert "submitted within 15 seconds" in d.cause
    assert d.recommended_mission_ids  # non-empty


def test_pending_prompt_quality_explained_as_pending() -> None:
    dims = {
        "prompt_quality": _DS(
            score=-1,
            max_score=10,
            signals=["prompt_quality_pending: judgement.score=None (error=llm_unavailable)"],
        )
    }
    narr = build_feedback_narrative(dimensions=dims)
    assert len(narr) == 1
    assert narr[0].score is None
    assert "pending" in narr[0].cause.lower()


def test_weakest_judge_axis_is_surfaced() -> None:
    """When the LLM judge ran but the supervisor scored low on one axis,
    the diagnostic names that axis specifically."""
    dims = {
        "prompt_quality": _DS(
            score=3,
            max_score=10,
            signals=[
                "averaged last 1 of 1 prompt(s) via LLM judge → 3/10",
                "judge[fresh] score=3/10 specificity=2.0/2.5 "
                "constraint=0.2/2.5 engagement=0.5/2.5 verifiability=0.3/2.5",
            ],
        )
    }
    narr = build_feedback_narrative(dimensions=dims)
    assert len(narr) == 1
    # constraint axis was the lowest → diagnostic about scope constraints
    assert "scope constraint" in narr[0].cause.lower() or "scope" in narr[0].cause.lower()


def test_strong_dimension_not_listed_as_weakness() -> None:
    dims = {"agent_review": _DS(score=15, max_score=15, signals=[])}
    narr = build_feedback_narrative(dimensions=dims)
    assert narr == []


def test_completed_missions_filtered_from_recommendations() -> None:
    """Don't recommend a mission the user has already done."""
    dims = {
        "safety": _DS(
            score=0,
            max_score=10,
            signals=["+0: new dependencies detected (or validator absent)"],
        )
    }
    # Both safety-relevant missions already completed.
    narr = build_feedback_narrative(
        dimensions=dims,
        completed_mission_ids=[
            "security-validation-removed",
            "dependency-misuse",
        ],
    )
    assert len(narr) == 1
    assert narr[0].recommended_mission_ids == []
    assert "already attempted" in narr[0].recommendation


def test_max_diagnostics_cap_honoured() -> None:
    """All seven dimensions weak → return at most max_diagnostics entries
    (default 4)."""
    dims = {
        name: _DS(score=0, max_score=10, signals=[])
        for name in (
            "final_correctness",
            "verification",
            "agent_review",
            "prompt_quality",
            "context_selection",
            "safety",
            "diff_minimality",
        )
    }
    narr = build_feedback_narrative(dimensions=dims, max_diagnostics=4)
    assert len(narr) == 4


def test_recommendation_version_is_pinned() -> None:
    """Bumping the recommendation set should be intentional — this test
    catches accidental changes."""
    assert RECOMMENDATION_VERSION == 1


def test_diagnostic_serialises_for_wire() -> None:
    d = Diagnostic(
        dimension="agent_review",
        score=3,
        max_score=15,
        cause="...",
        recommendation="...",
        recommended_mission_ids=["mission-a", "mission-b"],
    )
    data = d.to_dict()
    assert set(data.keys()) == {
        "dimension",
        "score",
        "max",
        "cause",
        "recommendation",
        "recommended_mission_ids",
    }
    assert data["max"] == 15
    assert data["recommended_mission_ids"] == ["mission-a", "mission-b"]
