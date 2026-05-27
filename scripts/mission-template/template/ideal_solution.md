# Ideal solution — $mission_id

TODO: write the reference solution as you'd narrate it to a thoughtful
junior. Cover, in this order:

1. **Reproduce.** How to trigger the failure mode `$failure_mode` from
   the visible test suite (or by hand). The canned agent patch should
   leave at least one hidden test red.
2. **Root cause.** One paragraph naming the exact line / contract /
   invariant the canned patch missed.
3. **Fix.** The minimum diff — paths, key edits, and why each is needed.
   Aim for a diff close to `expected_diff_lines_p50`.
4. **Regression test.** What the new test must assert, and where to put
   it so the project's existing test conventions are honoured.
5. **Verification.** Which commands to run before and after the fix,
   and what their output should look like.

When you finish, drop the unified diff into `ideal_solution.diff` (the
`extract_ideal_diffs.py` helper bundles it into the grader's golden
corpus).
