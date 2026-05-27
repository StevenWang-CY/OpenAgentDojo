# Mission 02 — Agent Edits the Wrong File

Author notes for content maintainers. Not user-facing.

| | |
|---|---|
| Repo pack | `fullstack-auth-demo` |
| Failure mode | `wrong_layer_committed` |
| Difficulty | beginner |
| Diff envelope (p50) | 6 lines |
| Score band, unmodified agent patch | 30–55 |
| Score band, ideal solution | ≥ 90 |

## Design intent

The whole mission is about *picking the right file*. The visible
symptom (truncated profile name) screams "CSS!" and the agent obliges
with `text-overflow: ellipsis` on `ProfileCard.tsx`. The actual bug is
in the backend serializer — `record.fullName.slice(0, 8)` — three
directories away from where the agent is editing.

Supervisors win by:

- selecting `backend/src/users/serialize.ts` in their context up front,
- refusing the agent's first patch and asking it to look at the API
  response payload,
- writing a regression test against `/api/users/me`.

Supervisors lose by:

- accepting the cosmetic fix verbatim,
- never opening the serializer file,
- adding `text-overflow` rules anywhere in the diff.

## Files in this folder

- `mission.yaml` — manifest.
- `agent_patch.diff` — one hunk in `frontend/src/ProfileCard.tsx`.
- `forbidden_changes.yaml` — `removes_serializer` and
  `frontend_css_only_fix`.
- `hidden_tests/` — vitest suite asserting the wire payload contains
  the full name.
- `ideal_solution.md` — the minimal backend serializer change.
- `prompts/` — agent narration + reasoning + intent keywords.
- `acceptance.yaml` — score envelopes.
