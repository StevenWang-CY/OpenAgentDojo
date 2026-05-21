# Mission 01 — Auth Cookie Expiration

Author notes. Not user-facing — the player sees `mission.yaml.brief`
inside the workspace, not this file.

## TL;DR

| | |
|---|---|
| Repo pack | `fullstack-auth-demo` |
| Failure mode | `checks_presence_not_expiration` |
| Difficulty | intermediate |
| Diff envelope (p50) | 18 lines |
| Score band, unmodified agent patch | 35–60 |
| Score band, ideal solution | ≥ 92 |

## Files in this folder

- `mission.yaml` — the manifest the grader + agent service read.
- `agent_patch.diff` — the deterministic, deliberately-flawed patch
  the agent applies. **One hunk**, narrows the existing presence check
  to also reject empty strings. Doesn't fix anything that wasn't
  already covered by `!raw`.
- `forbidden_changes.yaml` — `removes_middleware` (regex-absent on
  `requireAuth`) and `hardcoded_test_user`.
- `hidden_tests/`
  - `auth.hidden.test.ts` — 4 cases (expired, tampered, valid, refresh).
  - `runner.sh` — copies the test into `backend/src/tests/hidden/`
    and invokes `pnpm vitest run src/tests/hidden` with the JSON
    reporter for the grader to consume.
  - `package.json` — bare descriptor for the grader (the actual run
    uses the repo pack's installed vitest).
- `ideal_solution.md` — shown post-submit.
- `prompts/`
  - `response.md` — Jinja2 narration template (subtly wrong).
  - `reasoning.md` — Jinja2 chain-of-thought (skips `isValid()` on
    purpose).
  - `intents.yaml` — keyword sets for the 5 canonical intents.
- `acceptance.yaml` — score envelopes for the nightly self-test.

## Verifying this mission locally

From this folder:

```bash
# 1. Visible tests pass on the initial commit.
( cd ../_shared/repos/fullstack-auth-demo && pnpm test:unit && pnpm test:integration )

# 2. Hidden tests fail on the initial commit (3/4 fail).
TMP=$(mktemp -d) && cp -r ../_shared/repos/fullstack-auth-demo/. "$TMP/" \
  && mkdir -p "$TMP/backend/src/tests/hidden" \
  && cp hidden_tests/auth.hidden.test.ts "$TMP/backend/src/tests/hidden/" \
  && ( cd "$TMP" && cat > pnpm-workspace.yaml <<EOF
packages:
  - "backend"
  - "frontend"
EOF
       pnpm install --silent && cd backend && pnpm vitest run src/tests/hidden )
#   expect: 3 failed | 1 passed

# 3. Agent patch applies cleanly and the visible tests still pass.
( cd "$TMP" && git init -q && git add -A \
    && git -c user.email=t@t -c user.name=t commit -q -m init \
    && git apply ../../missions/01-auth-cookie-expiration/agent_patch.diff )

# 4. The agent patch doesn't fix the hidden tests — still 3 failed.
( cd "$TMP/backend" && pnpm vitest run src/tests/hidden )

# 5. Hand-apply the ideal solution → all 4 hidden tests pass.
#    (see ideal_solution.md for the diff)
```

## Known deviations from the spec

- `agent_patch.diff` does not modify `backend/src/routes/login.ts` even
  though the refresh hidden test exists. That's intentional: the agent
  is supposed to miss it entirely. The ideal solution does include the
  refresh fix.
- The patch is intentionally a single-line change. Real agents would
  add 3–10 lines for a "real fix"; we keep it tight so the
  `diff_minimality` validator stays neutral and the user is judged on
  *what* was changed, not *how much*.
