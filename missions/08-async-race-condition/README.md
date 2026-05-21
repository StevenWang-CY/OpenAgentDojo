# Mission 08 — Async Race Condition (Queue Processing)

| | |
|---|---|
| Repo pack | `data-api-demo` |
| Failure mode | `non_transactional_check_then_act` |
| Difficulty | advanced |
| Diff envelope (p50) | 8 lines |
| Score band, unmodified agent patch | 25–50 |
| Score band, ideal solution | ≥ 90 |

## Design intent

`claim_job` is a check-then-act between `session.get(Job)` and the
follow-up `UPDATE`. The visible single-worker tests pass; the
20-concurrent hidden test fails. The agent's "fix" wraps the
critical section in an `asyncio.Lock()` (created per-call, which
isn't even a lock — it locks an empty room), so the same race
persists. The right answer is a single transactional UPDATE filtered
on `status='pending'` plus a `rowcount` check.

This mission also tests **regression-test discipline**: the
`gather`-based concurrency test is the only thing that catches the
problem before it ships, and adding one is what separates a 50 score
from a 90.

## Files

- `mission.yaml`, `agent_patch.diff`, `forbidden_changes.yaml`
- `hidden_tests/test_jobs_hidden.py`, `hidden_tests/runner.sh`
- `ideal_solution.md`, `acceptance.yaml`
- `prompts/{response,reasoning}.md`, `prompts/intents.yaml`
