"""Prompt loader: renders Jinja2 vars and splits system / user halves.

Also pins that an unset template variable raises (StrictUndefined) so
a forgotten field surfaces as a contract bug rather than as the empty
string inside a prompt.
"""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from app.llm.prompt_loader import render_prompt


def test_renders_recommendation_diagnosis_with_variables() -> None:
    system, user = render_prompt(
        "recommendation_diagnosis",
        weakest_dim="agent_review",
        weakest_dim_avg=8.4,
        weakest_dim_attempts=3,
        rubric_version="v1",
    )
    # System half contains the variable substitutions.
    assert "agent_review" in system
    assert "8.4" in system
    assert "3 graded submissions" in system
    assert "`v1`" in system
    # User half is whitespace-only (the template marks the system / user
    # split with markers; the user half is empty for this template). The
    # loader strips both — confirm it returns clean strings, not None.
    assert isinstance(user, str)


def test_renders_recommendation_why_with_alignment() -> None:
    system, user = render_prompt(
        "recommendation_why",
        mission_id="auth-cookie-expiration",
        failure_mode_title="Session cookie outlives its declared TTL",
        weakest_dim="agent_review",
        alignment=0.92,
        dimension_summary="they read the agent's diff line-by-line",
    )
    assert "auth-cookie-expiration" in system
    assert "0.92" in system
    assert "agent_review" in system
    assert isinstance(user, str)


def test_unset_variable_raises_undefined_error() -> None:
    with pytest.raises(UndefinedError):
        render_prompt(
            "recommendation_diagnosis",
            weakest_dim="agent_review",
            # weakest_dim_avg intentionally omitted
            weakest_dim_attempts=3,
            rubric_version="v1",
        )


def test_unknown_template_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        render_prompt("nonexistent_template")


def test_critical_moment_polish_renders_seed_fields() -> None:
    system, _ = render_prompt(
        "critical_moment_polish",
        event_kind="diff.viewed",
        file_path="apps/api/app/auth/session.py",
        line_range_start=42,
        line_range_end=58,
        seed_explanation="you opened the diff but did not scroll past line 50",
        seed_what_to_do_instead="scroll to the end of the diff before approving",
    )
    assert "diff.viewed" in system
    assert "apps/api/app/auth/session.py" in system
    assert "42" in system and "58" in system


def test_scratchpad_coaching_renders_event_stream() -> None:
    # Wave 2B owns the canonical scratchpad_coaching template; vars are
    # ``notes`` (verbatim body), ``events_timeline`` (id + offset_seconds +
    # kind + summary tuples), ``failure_mode``, ``ideal_solution``, and
    # ``score_dimensions``. The system half is content-stable; the user
    # half carries every substitution.
    system, user = render_prompt(
        "scratchpad_coaching",
        notes="checked the failing test; not sure what changed",
        events_timeline=[
            {"id": 7, "offset_seconds": 60, "kind": "test_run", "summary": "pytest -x"},
            {"id": 12, "offset_seconds": 180, "kind": "diff_view", "summary": "auth.py"},
        ],
        failure_mode="Session cookie outlives its declared TTL",
        ideal_solution="Check Date.now() against session.expires_at.",
        score_dimensions={"agent_review": 6, "verification": 8},
    )
    # System half pins the coaching contract (anchor markers).
    assert "[event:N]" in system
    assert '[note:"<quote>"]' in system
    # User half carries every variable substitution.
    assert "checked the failing test" in user
    assert "id=7" in user and "60s" in user
    assert "id=12" in user and "180s" in user
    assert "Session cookie outlives its declared TTL" in user
    assert "Check Date.now()" in user
    assert "agent_review: 6" in user


def test_mission_authoring_draft_renders_seed_outline() -> None:
    system, _ = render_prompt(
        "mission_authoring_draft",
        repo_pack_id="go-orders-service",
        failure_mode_title="Goroutine leak on early return",
        seed_outline="- order handler\n- spawn goroutine for log shipping\n- early return on validation error",
    )
    assert "go-orders-service" in system
    assert "Goroutine leak" in system
    assert "order handler" in system
