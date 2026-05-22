"""Context selection scores from the selection that PRECEDED the LAST prompt.

If the supervisor selects all required files, submits a prompt, then clears
the context, the operative selection is the one made before the prompt.
The previous rule used the *last* selection event, which silently zeroed the
dimension after a post-submit cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.grading.score import _score_context_selection


@dataclass
class _ExpectedContext:
    required: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    discouraged: list[str] = field(default_factory=list)


@dataclass
class _Manifest:
    id: str = "context-selection-test"
    expected_context: _ExpectedContext = field(default_factory=_ExpectedContext)


def _evt(et: str, payload: dict[str, Any], occurred_at: str = "2026-05-22T10:00:00Z") -> dict:
    return {"event_type": et, "payload": payload, "occurred_at": occurred_at}


def test_uses_selection_before_last_prompt_not_the_final_clear() -> None:
    manifest = _Manifest(
        expected_context=_ExpectedContext(
            required=["src/a.ts", "src/b.ts"],
            recommended=["src/c.ts"],
        )
    )
    events = [
        _evt("context.selected", {"files": ["src/a.ts", "src/b.ts", "src/c.ts"]},
             "2026-05-22T10:00:00Z"),
        _evt("prompt.submitted", {"text": "fix this"},
             "2026-05-22T10:01:00Z"),
        # Post-prompt cleanup: user clears the context panel.
        _evt("context.selected", {"files": []},
             "2026-05-22T10:02:00Z"),
    ]
    score = _score_context_selection(events, manifest)
    # required_hit=1.0, recommended_hit=1.0 → round(7+3)=10.
    assert score.score == 10, (
        f"operative selection (before prompt) had full coverage; "
        f"got {score.score}; signals={score.signals}"
    )


def test_no_prompt_yet_falls_back_to_max_across_selections() -> None:
    manifest = _Manifest(
        expected_context=_ExpectedContext(required=["src/a.ts", "src/b.ts"])
    )
    events = [
        _evt("context.selected", {"files": ["src/a.ts"]},
             "2026-05-22T10:00:00Z"),
        # Better selection later — should be used because no prompt submitted.
        _evt("context.selected", {"files": ["src/a.ts", "src/b.ts"]},
             "2026-05-22T10:01:00Z"),
    ]
    score = _score_context_selection(events, manifest)
    # max recall over the two selections → full credit.
    assert score.score == 7, (
        f"max-across-selections fallback should yield 7 (required only); "
        f"got {score.score}; signals={score.signals}"
    )


def test_selection_after_prompt_only_does_not_contribute() -> None:
    """A selection made only after the prompt should not award credit."""
    manifest = _Manifest(
        expected_context=_ExpectedContext(required=["src/a.ts", "src/b.ts"])
    )
    events = [
        _evt("prompt.submitted", {"text": "fix this"},
             "2026-05-22T10:00:00Z"),
        # After-the-fact selection — operative selection is None.
        _evt("context.selected", {"files": ["src/a.ts", "src/b.ts"]},
             "2026-05-22T10:01:00Z"),
    ]
    score = _score_context_selection(events, manifest)
    # No operative selection before the prompt → score the empty selection.
    assert score.score == 0, (
        f"selections made only after the prompt must not count; "
        f"got {score.score}; signals={score.signals}"
    )
