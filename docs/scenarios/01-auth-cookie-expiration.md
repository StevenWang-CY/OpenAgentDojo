# Mission 01: Auth Cookie Expiration

- Mission id: `auth-cookie-expiration` (folder: `missions/01-auth-cookie-expiration/`)
- Difficulty: intermediate
- Category: auth
- Repo pack: `fullstack-auth-demo`
- Estimated minutes: 35
- Skills tested: auth, security, test-writing, agent-review
- Status: building (M1 reference mission)

## Why this scenario

Session-management bugs are among the most common, most user-impactful, and most easily overlooked errors in real applications. The OWASP top 10 ("Identification and Authentication Failures") consistently cites session-lifetime mishandling. A real coding agent — asked to fix "users with expired sessions can still access dashboard" — will frequently produce a patch that *appears* to fix the bug because the visible tests pass, but actually only checks that the cookie is present, not that it's valid.

This is the canonical "patch looks right; tests pass; bug remains" failure. It's the perfect first mission because it teaches the single most important supervisory habit: **read the diff. The visible tests are not enough.**

## Why this failure mode

The agent's patch (`agent_patch.diff`):

```diff
-  if (!raw) {
+  if (!raw || raw.length === 0) {
     return res.redirect("/login");
   }
   const session = parseSessionCookie(raw);
```

This is *plausible*: a senior reviewer skimming a diff might nod at "added length check." The visible test ("missing session redirects to login") passes. The narration the agent provides — templated from `prompts/response.md` — sounds confident: it talks about "guarding against empty cookies."

What it doesn't do: call `session.isValid(Date.now())`. The hidden test creates a session signed with `exp: Date.now() - 1000` and expects a redirect — and the patched middleware happily lets it through.

The failure is exactly the kind of subtle, real-feeling miss that a hurried supervisor would accept.

## What we expect users to learn

- **Open the diff after the agent applies a patch.** Don't trust the prose.
- **Re-run the relevant test suite after the agent's patch.** The visible suite is the floor, not the ceiling.
- **Write a regression test that names the failure mode.** "expired" is the missing word.
- **Constrain the agent's scope in the prompt.** "Keep the fix minimal, only touch the middleware" earns prompt-quality points and surfaces the right code path.

## Common mistakes

- **Accept the patch without opening the diff.** Time-pressure failure. The Agent Review dimension catches this with its 15-s "submitted without diff.opened" guard.
- **Run the wrong tests.** Running `pnpm test:unit` passes; running the integration suite (which our hidden tests live alongside) is what's needed. The `require_targeted_test: "auth"` reward signal nudges toward the right command.
- **Add the fix in the wrong layer.** Some users will edit `session.ts` to never produce an expired session, instead of fixing the middleware. The `must_touch_any_of` validator catches this.
- **Forget the regression test entirely.** This is the most common miss. `reward_signals.prompt_quality.must_include_any` includes `"regression test"` to reward asking for one.
- **Rewrite too much.** A user who refactors the whole auth module loses Diff Minimality points. `expected_diff_lines_p50: 18` keeps the budget tight.

## Expected score envelopes

| Submission | Expected score |
|---|---|
| Unmodified agent patch | 35–60 |
| Ideal solution | ≥ 92 |
| Empty submission | ≤ 12 |

A user who submits the agent's patch unchanged should land around 50: they get visible tests + no regression + root cause touched (18 capped), maybe verification + some context + safety. They lose final-correctness points to the hidden tests. The 35–60 envelope gives us room for noise around prompt and context selection.

A user who applies the ideal solution AND ran the auth tests AND added a regression test should clear 92.

An empty submission should score around 8–12: small context-selection points, no other process signals.

## Manifest sketch

```yaml
id: auth-cookie-expiration
title: "Expired Session Cookie Still Grants Access"
difficulty: intermediate
category: auth
estimated_minutes: 35
skills_tested: [auth, security, test-writing, agent-review]
failure_mode:
  id: checks_presence_not_expiration
  title: "Agent validates cookie existence but not expiration"
expected_context:
  required:
    - backend/auth/session.ts
    - backend/middleware/requireAuth.ts
  recommended:
    - backend/tests/auth.test.ts
    - docs/auth.md
  discouraged:
    - frontend/components/LoginForm.tsx
    - frontend/styles/login.css
reward_signals:
  prompt_quality:
    must_include_any: [reproduce, "root cause", "regression test", minimal, expiration]
  verification:
    require_targeted_test: "auth"
expected_diff_lines_p50: 18
```

The authoritative manifest is `missions/01-auth-cookie-expiration/mission.yaml`.

## Ideal-solution sketch

```diff
   const session = parseSessionCookie(raw);
+  if (!session || !session.isValid(Date.now())) {
+    return res.redirect("/login");
+  }
   req.session = session;
```

Plus a regression test in `backend/tests/auth.test.ts` that signs a session with `exp` in the past and asserts the redirect.

## Open questions

- Should we also break a refresh-token edge case in a separate hidden test? Today the failure is single-axis (expiration). Splitting it into 4 hidden tests (per `mission.yaml.hidden_tests.expected_pass`) gives us granular failure reporting in the score signals.
- Should the agent's prose explicitly *mention* expiration to bait the user? Current template avoids it; we want them to notice the gap themselves.

## References

- OWASP Top 10 — A07:2021 Identification and Authentication Failures.
- [IMPLEMENTATION_PLAN.md §7](../../IMPLEMENTATION_PLAN.md) (manifest spec)
- [IMPLEMENTATION_PLAN.md §14.1](../../IMPLEMENTATION_PLAN.md) (mission spec)
- [IMPLEMENTATION_PLAN.md §30](../../IMPLEMENTATION_PLAN.md) (example agent patch)
- [IMPLEMENTATION_PLAN.md §31](../../IMPLEMENTATION_PLAN.md) (example ideal solution)
- [docs/grading.md — worked example](../grading.md)
