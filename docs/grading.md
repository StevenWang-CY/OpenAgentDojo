# Grading Deep-Dive

The score is the product's pedagogical signal. This doc walks through how it's computed end-to-end, with a worked example on Mission 01. The rubric source-of-truth is [IMPLEMENTATION_PLAN.md §11](../IMPLEMENTATION_PLAN.md); the schema is [docs/schemas/score_report.schema.json](./schemas/score_report.schema.json); the why is [ADR 0006](./adr/0006-scoring-rubric.md).

## Thesis: process matters

A user who submits a perfect patch by luck on attempt one should not score higher than a user who selected the right context, ran the relevant tests, opened the diff, asked for a regression test, and shipped an equivalent patch. The rubric makes that explicit: of 100 available points, **70 are process** (Verification + Agent Review + Prompt + Context + Safety + Minimality) and **30 are outcome** (Final Correctness).

But process without outcome is also incomplete. The hidden-test cap (next section) prevents 100% process supervisors from clearing the mission without a working fix.

## Weights at a glance

| Dimension | Max | Cap on failure |
|---|---|---|
| Final Patch Correctness | 30 | Hard-capped at **18** when any hidden test fails |
| Verification Discipline | 15 | — |
| Agent Output Review | 15 | Hard-capped at **0** if submitted within 15 s of `agent.responded` with no `diff.opened` |
| Prompt Quality | 10 | — |
| Context Selection | 10 | — |
| Safety Awareness | 10 | — |
| Diff Minimality | 10 | — |

The runtime source of truth is [`apps/api/app/grading/dimensions.py`](../apps/api/app/grading/dimensions.py); mission YAMLs declare the same numbers and the JSON schema pins them with `const`. A `tests/test_implementation_plan_rubric_invariant.py` Pytest fails CI if the plan and the constants drift apart. See [ADR 0011](./adr/0011-rubric-rebalance.md) for the rebalance history (verification dropped from `20` and diff_minimality rose from `5`).

The grading total is post-processed by a **score cap**: when `submissions.score_cap_reason = "gave_up"` ([ADR 0010](./adr/0010-give-up-policy.md)) the total clamps at `50/100`. Dimension scores stay honest; the report carries both `total` and `uncapped_total` so the FE can render "would have scored X" beside the capped value.

## How a submission gets scored

When the user clicks **Submit**:

1. The sandbox writes are frozen (`/workspace` set read-only).
2. The unified diff `git diff <initial_commit>..HEAD` is captured.
3. `/grader` is mounted read-only into the container.
4. The grader runs, in order:
   - Visible test suite (`mission.repo.test_commands.unit`).
   - Typecheck (`...test_commands.typecheck`).
   - Lint (`...test_commands.lint`).
   - Hidden test suite (`mission.hidden_tests.command`).
   - Static validators (`forbidden_changes`, `diff_scope`, `regression_test_required`, …).
5. The scoring engine consumes the validator results, the test results, and the `supervision_events` log to compute each dimension.
6. `submissions.score_report` is persisted; a `submission.graded` event is appended.
7. The sandbox is destroyed; artifacts (stdout, stderr, diff) ship to S3.

## Per-dimension breakdown

### Final Patch Correctness — 30

```
+ 12 if all hidden tests pass
+ 8  if all visible tests pass
+ 6  if no regression (existing tests still pass)
+ 4  if root cause addressed (required code paths touched per validator)
```

**Floor:** when `hidden_tests` are not all green, the dimension is capped at **18**. This means a submission can earn the visible-test, no-regression, and root-cause-touched signals (8+6+4=18) but cannot collect the 12-point "all hidden tests pass" bonus without actually passing them.

### Verification Discipline — 15

```
+ 6 if a targeted test ran AND either (a) it passed, or (b) it failed but the
     supervisor followed up with file.edited / file.reverted / prompt.submitted
     before submit (engagement-after-failure split)
+ 3 if a targeted test ran and failed with no follow-up edit / prompt
+ 3 if typecheck ran
+ 2 if lint ran
+ 4 if a NEW regression test exists in the final diff (regression_test_required validator passes)
- 6 if submitted with zero verification commands
```

Cap at 15. The engagement-after-failure split is the canonical word of [`_score_verification`](../apps/api/app/grading/score.py) — running a test and ignoring the red bar shouldn't earn the same credit as running it and acting on the signal.

### Agent Output Review — 15

```
+ 6 if diff.opened event exists after patch.applied
+ 5 if user edited or reverted any line of the agent's patch
+ 4 if user submitted at least one corrective prompt (intent ∈ {revise, narrow, test})
0  if submitted within 15 s of agent.responded with no diff.opened
```

The 0 ceiling enforces a "you must at least look at what the agent did" floor.

### Prompt Quality — 10

Computed per turn, taking the max across turns then clamping to 10:

```
score = 0
+ 2 if length >= 80 chars
+ 2 if contains any of mission.reward_signals.prompt_quality.must_include_any
+ 1 per bonus_keyword present (max +3)
+ 2 if request for tests / regression coverage is present
+ 2 if scope-constraint phrase present ("do not modify", "minimal", "without changing")
- 3 if length < 40 chars
- 2 if vague-only ("fix it", "make it work")
```

Mission authors tune `must_include_any` and `bonus_keywords` per scenario. The check is case-insensitive.

### Context Selection — 10

```
required_hit = |selected ∩ required| / |required|
recommended_hit = |selected ∩ recommended| / |recommended|
discouraged_hit = |selected ∩ discouraged|

score = round(required_hit * 7 + recommended_hit * 3) - min(3, discouraged_hit)
clamp(0, 10)
```

`required`, `recommended`, and `discouraged` come from `mission.yaml.expected_context`. The score reflects whichever `context.selected` event was most recent before the first `prompt.submitted`.

### Safety Awareness — 10

```
+ 4 if no forbidden_changes triggered
+ 3 if user reverted/rewrote a forbidden change introduced by the agent
+ 2 if no new dependencies added (or only allowlisted ones)
+ 1 if no banned commands run (rm -rf, curl | sh, etc.)
```

The "reverted/rewrote" signal is detected by checking for `file.edited` or `file.reverted` events on a path the `forbidden_changes` validator initially flagged on the unmodified agent diff.

### Diff Minimality — 10

Symmetric-churn scale against `mission.expected_diff_lines_p50` (clamped to `[3, 200]` at scoring time so a typoed manifest can't silently shift the band):

```
churn = max(added_lines, removed_lines)   # both directions count
score = 0  if churn == 0                  # empty submissions earn no minimality credit
ratio = churn / expected_p50

ratio ≤ 1.0  → 10
ratio ≤ 1.5  →  8
ratio ≤ 2.0  →  6
ratio ≤ 3.0  →  4
ratio  > 3.0 →  0
```

Using `max(added, removed)` (not just added) prevents destructive minimisation — wiping out 200 lines while adding 5 — from being rewarded as "minimal."

## Worked example — Mission 01

**Scenario:** "Expired Session Cookie Still Grants Access." The agent's patch adds a presence check but does not call `session.isValid()`. The user spotted it, asked for a regression test, ran the auth suite (green), and shipped a corrected patch — but skipped typecheck and missed the refresh-token edge case.

### Events recorded

```
session.started
context.selected       { files: ["backend/auth/session.ts", "backend/middleware/requireAuth.ts"] }
prompt.submitted       { text: "Investigate the expired-session bug. Add a regression test for expiration and keep the fix minimal." }
agent.responded        { intent: "fix" }
patch.applied          { files_changed: 1, added: 1, removed: 1 }
diff.opened            { path: "backend/middleware/requireAuth.ts" }
file.edited            { path: "backend/middleware/requireAuth.ts", added: 4, removed: 0 }
file.edited            { path: "backend/tests/auth.test.ts", added: 8, removed: 0 }
command.run            { command: "pnpm test:integration auth", category: "test", exit_code: 0 }
submission.requested
```

### Scoring

| Dimension | Signal | Score |
|---|---|---|
| Final Correctness | Visible green (+8), no regression (+6), root cause touched (+4), but hidden test "session refresh respects expiration" fails → capped at 18 | **18 / 30** |
| Verification | Targeted auth test ran and passed (+6), regression test added (+4), no typecheck (0), no lint (0) | **10 / 15** |
| Agent Review | `diff.opened` after `patch.applied` (+6), edited the agent's line (+5), no corrective prompt (0) | **11 / 15** |
| Prompt Quality | ≥80 chars (+2), contains "expiration" + "regression test" (+2), bonus "minimal" (+1), regression-test phrasing (+2), scope phrase "keep the fix minimal" (+2) → cap 10 | **9 / 10** |
| Context Selection | required_hit = 2/2 → 7, recommended_hit = 0/2 → 0, discouraged_hit = 0 → 0 | **7 / 10** |
| Safety | No forbidden change triggered (+4), no deps added (+2), no banned commands (+1) | **7 / 10** |
| Diff Minimality | churn = max(12, 0) = 12 vs p50 of 18 → ratio 0.67 → 10 | **10 / 10** |
| **Total** | | **72 / 100** |

The user passed the visible bar but missed the refresh-token edge case in the hidden suite. The report highlights:

- **Strengths:** "Selected the right context up front", "Asked for a regression test".
- **Weaknesses:** "Did not run typecheck", "Missed the refresh-token edge case".
- **Badges earned:** `regression-test-writer` (added a regression test matching the failure-mode keyword `expiration`).

### What the report JSON looks like

```json
{
  "rubric_version": "v1",
  "total": 72,
  "effective_max": 100,
  "score_cap_reason": null,
  "uncapped_total": null,
  "dimensions": {
    "final_correctness":  { "score": 18, "max": 30, "signals": ["visible green", "no regression", "root cause touched", "hidden tests not all green — capped"] },
    "verification":       { "score": 10, "max": 15, "signals": ["targeted auth test ran and passed", "regression test added", "no typecheck"] },
    "agent_review":       { "score": 11, "max": 15, "signals": ["diff opened", "edited agent's line"] },
    "prompt_quality":     { "score": 9,  "max": 10, "signals": ["mentions regression test", "scoped", "uses keyword 'expiration'"] },
    "context_selection":  { "score": 7,  "max": 10, "signals": ["selected both required files"] },
    "safety":             { "score": 7,  "max": 10, "signals": ["no forbidden change triggered", "no new deps"] },
    "diff_minimality":    { "score": 10, "max": 10, "signals": ["churn = 12 vs p50 18 → ratio 0.67"] }
  },
  "strengths": ["Selected the right context up front", "Asked for a regression test"],
  "weaknesses": ["Did not run typecheck", "Missed the refresh-token edge case"],
  "missed_failure_mode": false,
  "badges_earned": ["regression-test-writer"]
}
```

`effective_max` ([P0-1 / P0-2](../P0_DESIGN.md)) drops below 100 when a dimension is **pending** (the cached prompt judge hasn't returned a verdict yet). `score_cap_reason` + `uncapped_total` are emitted by the give-up path ([ADR 0010](./adr/0010-give-up-policy.md)).

## Determinism

Replaying the same event stream against the same mission manifest MUST produce the identical `score_report`. The nightly determinism test (§19.2) asserts 5/5 identical reports per mission. Sources of nondeterminism are excluded by construction:

- The grader does not invoke an LLM.
- Random and time-based logic in scoring code is forbidden by the §29.3 PR checklist.
- Test runners use pinned dependency lockfiles inside frozen base images.

## What the user does *not* see pre-submit

`ScorePreview` shows partial process signals during the session — but never:

- Hidden test names or counts.
- Failure-mode hints.
- A predicted total.

See [IMPLEMENTATION_PLAN.md §13.5](../IMPLEMENTATION_PLAN.md) for the full live-preview policy.
