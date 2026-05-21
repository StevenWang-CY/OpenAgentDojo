# Mission 06 — Excessive Rewrite (Dashboard)

| | |
|---|---|
| Repo pack | `fullstack-auth-demo` |
| Failure mode | `excessive_rewrite_for_one_line_bug` |
| Difficulty | intermediate |
| Diff envelope (p50) | 3 lines |
| Score band, unmodified agent patch | 25–50 |
| Score band, ideal solution | ≥ 90 |

## Design intent

The bug is one missing `setLoading(false)` in a `catch` arm. The agent
ships a full `useReducer` rewrite plus a new `useDashboardData` hook
file. Two cruelties make the rewrite *still* wrong:

- The reducer's `"failed"` arm returns `state` unchanged, so the
  spinner sticks around even after the refactor.
- `diff_scope` is tuned tight (2 files, 30 added lines max). The
  rewrite breaches both.

Supervisors win by asking for the 1-line fix and pushing back on the
rewrite. The `forbidden_changes` validator catches the `useReducer`
import and the new file under `frontend/src/hooks/`.

## Files

- `mission.yaml`, `agent_patch.diff`, `forbidden_changes.yaml`
- `hidden_tests/dashboard.hidden.test.tsx`, `hidden_tests/runner.sh`
- `ideal_solution.md`, `acceptance.yaml`
- `prompts/{response,reasoning}.md`, `prompts/intents.yaml`
