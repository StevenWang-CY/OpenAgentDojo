# Calibration Set

A ground-truth dataset for the grading engine. Each YAML in this folder
pins **per-dimension expected scores** for a deterministic replay of one
of three standard scenarios on one mission:

- **`unmodified`** — supervisor accepts the agent patch verbatim and
  submits. Models the worst case: no review, no fix, no test run.
- **`ideal`** — supervisor produces the minimal correct fix, runs the
  targeted test, opens the diff, writes a regression test, and submits.
  Models the best case.
- **`empty`** — supervisor submits the original codebase with no patch
  applied at all. Models the "blank" baseline.

For each scenario the calibration file declares an expected total and a
per-dimension breakdown. `apps/api/tests/missions/test_calibration.py`
reconstructs the inputs (via the same fixture builders the
acceptance-envelope tests use), runs the grading engine, and asserts:

- `|actual_total - expected_total| <= TOTAL_TOLERANCE` (5 points)
- `|actual_dim_score - expected_dim_score| <= DIMENSION_TOLERANCE`
  (2 points per dimension)

This is the regression gate that catches *unintended* scoring drift —
any change to `apps/api/app/grading/` that shifts a calibration scenario
by more than the tolerance must be paired with an intentional calibration
update.

## Bootstrap baseline vs human-grader ground truth

The expected scores currently shipped in this folder are **author-asserted
bootstrap baselines**, captured by running the current scoring engine
against each scenario and recording the output. They lock in *the
grader's current behaviour* against future regressions, but they do NOT
constitute scientifically validated ground truth.

The next-phase upgrade is to replace the bootstrap baseline with
**human-grader medians**: at least two independent expert supervisors
score each replay session manually, the inter-rater agreement
(Krippendorff's α) is computed, and the median of their per-dimension
scores becomes the new expected value. When that data lands, the
`source` field on each baseline switches from `bootstrap` to
`human_median` and a `graders` array carries the per-grader scores plus
the α.

## File layout

```
missions/_calibration/
  README.md              ← this file
  <mission_id>.yaml      ← one per mission, e.g. auth-cookie-expiration.yaml
```

Each `<mission_id>.yaml` schema:

```yaml
mission_id: auth-cookie-expiration
mission_version: 1                    # bump to invalidate cached baselines
scenarios:
  - name: unmodified | ideal | empty
    expected_total: 50
    source: bootstrap | human_median
    dimensions:
      final_correctness: 18
      verification: 7
      agent_review: 0
      prompt_quality: 4
      context_selection: 7
      safety: 5
      diff_minimality: 9
    graders: []                       # populated once humans score this
```

## Adding a new scenario

1. Add a new fixture builder to `apps/api/tests/missions/_fixtures.py`
   (mirroring the shape of `build_unmodified_submission`).
2. Update each mission's `<mission_id>.yaml` with the new scenario name.
3. Run `uv run pytest tests/missions/test_calibration.py -v` to see the
   actual grader output; copy the per-dimension scores into the YAML.
4. Commit. The bootstrap baseline is now locked in.

## Upgrading to human graders

1. Pick a scenario from one mission.
2. Have two experts independently score the replay manually, recording
   per-dimension scores + rationale in `graders: [...]`.
3. Compute Krippendorff's α across the two graders' scores.
4. Replace the bootstrap `dimensions:` with the median of the two
   grader scores.
5. Set `source: human_median`.
6. The calibration test will catch any scoring-engine change that
   diverges from the human median by more than the tolerance.
