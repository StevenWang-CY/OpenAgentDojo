# ADR 0006: Scoring Rubric — Weighted-30 Over Flat-15

- Status: Accepted (weight table superseded in part by [ADR 0011](0011-rubric-rebalance.md): Verification 20→15, Diff Minimality 5→10)
- Date: 2026-05-21
- Deciders: OpenAgentDojo team

## Context

The product definition offered two rubric shapes:

- **Flat-15:** seven dimensions, each weighted approximately 15/100, totaling 105 capped at 100.
- **Weighted-30:** seven dimensions with non-uniform weights, the largest being Final Patch Correctness at 30.

The choice has product consequences. A flat rubric tells users every dimension is equally important; a weighted rubric encodes a hierarchy. We want the rubric to reflect the platform's thesis: "supervision quality matters, but the patch still has to be correct."

## Decision

Adopt **Weighted-30**:

| Dimension | Max |
|---|---|
| Final Patch Correctness | 30 |
| Verification Discipline | 20 |
| Agent Output Review | 15 |
| Prompt Quality | 10 |
| Context Selection | 10 |
| Safety Awareness | 10 |
| Diff Minimality | 5 |
| **Total** | **100** |

> The Verification (20) and Diff Minimality (5) weights above are the
> *original* M-series decision. They were later rebalanced to **15** and
> **10** respectively by [ADR 0011](0011-rubric-rebalance.md); the current
> runtime source of truth is `apps/api/app/grading/dimensions.py`. The rest
> of this ADR is preserved as the point-in-time rationale for the
> weighted-30 *shape*.

These weights are constants enforced by `mission.schema.json` (`const` per dimension). A mission that ships with different weights fails CI validation.

**Hidden-test cap:** When hidden tests fail, Final Patch Correctness is capped at **18**, regardless of how green the visible suite and validators look. This prevents a "lucky guess" submission from clearing 90 points by gaming the process signals.

## Consequences

### Positive

- The rubric communicates priorities: shipping a working fix is the biggest single dimension; process work makes up the rest.
- The 18-point cap closes the "all process, no outcome" loophole.
- Process dimensions (Verification + Agent Review + Prompt + Context + Safety + Minimality = 70 points) are still the majority — the platform's thesis holds.
- Weights are easy to reason about in the UI: each dimension's bar shows `score / max`.

### Negative

- Weight rebalancing is a breaking change — every existing submission's `score_report` becomes incomparable to new ones. We version the rubric inside `score_report.rubric_version` (default `"v1"`) so historical reports stay interpretable.
- New dimensions (e.g. "agent skepticism" as a standalone score) require a coordinated update to the schema, the engine, and every mission manifest.

### Neutral

- Per-mission weight overrides are explicitly disallowed at MVP; if we need them later, we'll add `scoring_weights_override` with a guard that the total still equals 100.

## Alternatives considered

- **Flat-15 (105 capped at 100).** Cleaner story, but doesn't communicate the "correctness matters most" signal, and the capping creates ambiguous breakdowns (105 → 100, which dimension lost the 5?).
- **Pure outcome-based (100% correctness).** Rejected — defeats the product purpose.
- **Pure process-based (0 weight on correctness).** Rejected — would let a thoughtful supervisor pass a mission while shipping a broken patch.
- **User-tunable weights.** Out of scope; we want comparable scores across users.

## References

- [IMPLEMENTATION_PLAN.md §11](../../IMPLEMENTATION_PLAN.md)
- [docs/grading.md](../grading.md)
- [docs/schemas/score_report.schema.json](../schemas/score_report.schema.json)
