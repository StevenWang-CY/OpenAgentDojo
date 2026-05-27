# Mission 09 — API Contract Drift

| | |
|---|---|
| Repo pack | `fullstack-auth-demo` |
| Failure mode | `api_contract_drift` |
| Difficulty | intermediate |
| Diff envelope (p50) | 16 lines |
| Score band, unmodified agent patch | 30–55 |
| Score band, ideal solution | ≥ 90 |

## Design intent

Backend silently renamed `fullName` -> `displayName`. Frontend
components `ProfileCard`, `Header`, and `Settings` still read
`fullName`. The agent grep's for the failing component (ProfileCard),
patches it, and ships — Header and Settings still render `undefined`.

A supervisor wins by asking for `grep -rn fullName frontend/src` and
ensuring every hit is addressed, then dropping the `fullName?: string`
alias so the type system enforces the rename going forward.

## Files

- `mission.yaml`, `agent_patch.diff`, `forbidden_changes.yaml`
- `hidden_tests/contract.hidden.test.tsx`, `hidden_tests/runner.sh`
- `ideal_solution.md`, `acceptance.yaml`
- `prompts/{response,reasoning}.md`, `prompts/intents.yaml`
