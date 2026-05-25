"""Verification dimension requires follow-up after a failing test (P1-1).

Pre-P1-1 the verification dimension awarded full credit (+6) for *any*
targeted test command run, regardless of whether the supervisor read the
output. A user who ran ``pytest`` once with red output and submitted
anyway scored identically to a user who fixed the test failure. The new
rule:

* test passed → +6 (engagement implicit; the supervisor moved on
  because the test was green).
* test failed AND was followed by a file.edited / file.reverted /
  prompt.submitted event → +6 (the supervisor demonstrably engaged).
* test failed with no follow-up → +3 partial credit.
* no targeted test ran → 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.grading.score import _score_verification


@dataclass
class _Verification:
    require_targeted_test: str | None = None


@dataclass
class _RewardSignals:
    verification: _Verification = field(default_factory=_Verification)


@dataclass
class _Manifest:
    id: str = "verification-followup"
    reward_signals: _RewardSignals = field(default_factory=_RewardSignals)


def _cmd(command: str, exit_code: int) -> dict:
    return {
        "event_type": "command.run",
        "payload": {
            "command": command,
            "category": "test",
            "exit_code": exit_code,
        },
    }


def test_passing_test_awards_full_credit() -> None:
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    events = [_cmd("pnpm test auth", exit_code=0)]
    ds = _score_verification(events, [], manifest)
    assert any("targeted test command run (and passed)" in s for s in ds.signals)
    # +6 (test) + 0 typecheck + 0 lint + 0 regression-validator + 0 commands
    # No -6 since command.run exists.
    assert ds.score == 6


def test_failing_test_with_followup_edit_awards_full_credit() -> None:
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    events = [
        _cmd("pnpm test auth", exit_code=1),
        {"event_type": "file.edited", "payload": {"path": "src/auth.ts"}},
    ]
    ds = _score_verification(events, [], manifest)
    assert any("followed up with file.edited" in s for s in ds.signals)
    assert ds.score == 6


def test_failing_test_with_followup_prompt_awards_full_credit() -> None:
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    events = [
        _cmd("pnpm test auth", exit_code=2),
        {"event_type": "prompt.submitted", "payload": {"text": "the auth test failed because..."}},
    ]
    ds = _score_verification(events, [], manifest)
    assert any("followed up with prompt.submitted" in s for s in ds.signals)
    assert ds.score == 6


def test_failing_test_without_followup_awards_partial_credit() -> None:
    """The headline P1-1 case: running a test and ignoring its output is
    not the same as running a test and acting on the result."""
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    events = [_cmd("pnpm test auth", exit_code=1)]
    ds = _score_verification(events, [], manifest)
    assert any("partial credit only" in s for s in ds.signals)
    assert ds.score == 3


def test_no_targeted_test_awards_zero_credit() -> None:
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    # A test ran but doesn't match the targeted pattern.
    events = [_cmd("pnpm test unrelated", exit_code=0)]
    ds = _score_verification(events, [], manifest)
    assert any("no targeted test command found" in s for s in ds.signals)
    # Still earns nothing for targeted (only the -6 penalty avoidance from
    # having at least one command.run).
    assert ds.score == 0


def test_mixed_runs_with_eventual_pass_awards_full_credit() -> None:
    """Realistic flow: run → see failure → edit → re-run → pass."""
    manifest = _Manifest(
        reward_signals=_RewardSignals(verification=_Verification(require_targeted_test="auth"))
    )
    events = [
        _cmd("pnpm test auth", exit_code=1),
        {"event_type": "file.edited", "payload": {"path": "src/auth.ts"}},
        _cmd("pnpm test auth", exit_code=0),
    ]
    ds = _score_verification(events, [], manifest)
    assert any("(and passed)" in s for s in ds.signals)
    assert ds.score == 6
