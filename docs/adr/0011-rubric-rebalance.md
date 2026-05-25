# ADR 0011 — Rubric rebalance (verification 20→15, diff_minimality 5→10)

- Status: Accepted
- Date: 2026-05-25
- Resolves: drift between [IMPLEMENTATION_PLAN.md §11.1](../../IMPLEMENTATION_PLAN.md) and `apps/api/app/grading/dimensions.py` (P0-13)
- Related: [ADR 0006 — scoring rubric](0006-scoring-rubric.md), [ADR 0010 — give-up policy](0010-give-up-policy.md)

## Context

The original rubric in ADR 0006 weighted **Verification Discipline at 20**
and **Diff Minimality at 5** out of a 100-point total. M5 calibration
against the `missions/_calibration/` envelopes surfaced two problems:

1. **Verification was over-credited.** "Ran a test" is a binary signal,
   and the +6 / +3 / +2 / +4 / −6 sub-scoring already saturates near 15
   for any conscientious attempt. The extra five points pushed weak
   submissions (verification 18, correctness 14) above strong-but-
   surgical submissions (verification 10, correctness 24, minimality 5)
   in a way the calibration set flagged as incorrect — the surgical
   attempt was visibly better supervision work.
2. **Diff Minimality was under-credited.** A surgical diff is one of
   the cleanest signals that the supervisor reasoned through the change
   before pressing apply. Weighting it at 5 made it indistinguishable
   from rounding error in the radar, even though calibration showed
   minimality correlated strongly with overall quality.

The shipped scorer in `apps/api/app/grading/score.py` already moved to
`verification=15 / diff_minimality=10` during M5; the plan and the
calibration narrative did not. This ADR resolves the drift and pins
the canonical numbers behind a single source of truth.

## Decision

1. **Move 5 points from Verification → Diff Minimality.** Final
   weights:

   | Dimension | Max |
   |---|---|
   | Final Patch Correctness | 30 |
   | Verification Discipline | **15** |
   | Agent Output Review | 15 |
   | Prompt Quality | 10 |
   | Context Selection | 10 |
   | Safety Awareness | 10 |
   | Diff Minimality | **10** |
   | **Total** | **100** |

2. **`apps/api/app/grading/dimensions.py` is the single source of
   truth.** Every other surface (mission YAML schema, IMPLEMENTATION_PLAN
   §11, the radar in the FE, the prose dimension labels) reads from or
   reconciles to that module. A new pytest invariant
   (`tests/test_implementation_plan_rubric_invariant.py`) fails the
   build if the plan and the module disagree.

3. **No re-scoring of historical submissions.** Submissions graded
   under earlier weights keep their original totals. The
   [P0-11 verification envelope](../../P0_DESIGN_11_13.md) stamps
   `rubric_version` onto every signed submission so an old PDF still
   verifies under its own historical rubric. A re-balance bumps
   `RUBRIC_VERSION`; old envelopes verify against `v1`, new ones against
   `v2`. (At this writing `RUBRIC_VERSION = "v1"` covers every shipped
   weight including this rebalance — the M5 → P0 change is contiguous.)

4. **Sub-scoring rules also re-aligned.** `_score_verification` is the
   canonical word on the engagement-after-failure split (+6 vs +3),
   and `_score_diff_minimality` is canonical on the
   `max(added, removed)` symmetric churn measure and the
   `churn = 0 → 0/10` floor. The plan's §11.2.2 and §11.2.7 prose
   mirror those scorers explicitly.

## Consequences

- **Honest tradeoff.** Strong-but-surgical attempts now score
  measurably higher than weak-but-thorough attempts, which is the
  calibration target. Weak-thorough is still a positive learning
  outcome ("at least they ran the tests") but no longer leads the
  rubric.
- **Mission YAMLs do not need to change.** The mission schema's
  `scoring_weights` block is a documentation surface; the code
  constants win. (A future iteration will move `scoring_weights` from
  required-field to schema-pinned const to reflect this — out of scope
  for this ADR.)
- **Existing PDFs verify under their original rubric.** The verify
  page renders a small note when `current_rubric_version != stamped`
  ("Scored under rubric v1 (current: v2)"). That path is exercised
  the first time we ship a v2.
- **No FE change required.** The radar reads dimension `max` from the
  score report payload, not from a hardcoded constant. The breakdown
  pills already render `score / max` per-dimension.
- **The invariant test is the load-bearing guarantee.** Any future
  rebalance must update *both* `dimensions.py` and IMPLEMENTATION_PLAN.md
  §11.1 — the test fails CI otherwise. The fix is structural drift
  prevention, not a one-shot reconciliation.

## What would change our mind

- Calibration data showing the new weights still under-credit a
  strong-supervision pattern we care about (e.g. context-selection
  consistently swung too far by missing a discouraged file). The
  rebalance is reversible; the constants live in one place.
- Telemetry shows users gaming diff minimality (e.g. squeezing every
  fix into a one-line monstrosity). That's a sub-scoring problem —
  tighten `_score_diff_minimality`'s ratio bands rather than reverting
  the weight.

## See also

- `apps/api/app/grading/dimensions.py` — single source of truth.
- `apps/api/app/grading/score.py` — `_score_verification`,
  `_score_diff_minimality` (the canonical scorers).
- `apps/api/tests/test_implementation_plan_rubric_invariant.py` —
  drift-prevention test.
- [IMPLEMENTATION_PLAN.md §11](../../IMPLEMENTATION_PLAN.md) — prose
  mirror (now reconciled).
- [P0_DESIGN_11_13.md §P0-13](../../P0_DESIGN_11_13.md) — design that
  motivated this reconciliation pass.
