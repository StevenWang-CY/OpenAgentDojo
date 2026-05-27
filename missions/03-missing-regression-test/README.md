# Mission 03 — Missing Regression Test (Duplicate Submissions)

| | |
|---|---|
| Repo pack | `fullstack-auth-demo` |
| Failure mode | `missing_regression_test` |
| Difficulty | intermediate |
| Diff envelope (p50) | 24 lines |
| Score band, unmodified agent patch | 35–58 |
| Score band, ideal solution | ≥ 90 |

## Design intent

This mission is *about writing the test*. The agent ships a plausible
in-memory `Set<string>` guard that satisfies the happy-path integration
test. Two hidden behaviours catch it:

1. concurrent requests racing past the `.has()` check before `.add()`,
2. a "fresh process" (simulated by `_resetForTests` + a new `createApp`)
   that wipes the Set without wiping the underlying store.

The ideal fix is a uniqueness guard *in the store* — the single place
every writer goes through — plus a regression test that fires the same
formId twice. That test is what `regression_test_required` looks for.

## Files

- `mission.yaml`, `agent_patch.diff`, `forbidden_changes.yaml`
- `hidden_tests/submissions.hidden.test.ts`, `hidden_tests/runner.sh`
- `ideal_solution.md`, `acceptance.yaml`
- `prompts/{response,reasoning}.md`, `prompts/intents.yaml`
