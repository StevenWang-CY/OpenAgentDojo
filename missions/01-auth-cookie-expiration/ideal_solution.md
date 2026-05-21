# Mission 01 — Ideal Solution

Shown in the post-mission report only **after** submit. Pairs the
minimal correct diff with a narrative walkthrough of what the agent got
wrong and what a strong supervisor would have caught.

## Root cause (in one sentence)

`Session.isValid()` exists, is unit-tested, and is the canonical
expiration check — but no caller is wired up to it, so any cookie with
a valid HMAC signature passes `requireAuth` regardless of its `exp`.

## Minimal diff

```diff
--- a/backend/src/middleware/requireAuth.ts
+++ b/backend/src/middleware/requireAuth.ts
@@ -24,8 +24,15 @@ export function requireAuth(req: Request, res: Response, next: NextFunction) {
   const raw = req.cookies?.session;
   if (!raw) {
     return res.redirect("/login");
   }
-  const session = parseSessionCookie(raw);
-  req.session = session ? new Session(session) : undefined;
+  const parsed = parseSessionCookie(raw);
+  if (!parsed) {
+    return res.redirect("/login");
+  }
+  const session = new Session(parsed);
+  if (!session.isValid(Date.now())) {
+    return res.redirect("/login");
+  }
+  req.session = session;
   next();
 }
```

```diff
--- a/backend/src/routes/login.ts
+++ b/backend/src/routes/login.ts
@@ -43,6 +43,9 @@ loginRouter.post("/login/refresh", (req, res) => {
   const parsed = parseSessionCookie(raw);
   if (!parsed) {
     return res.status(401).json({ ok: false, status: "invalid" });
   }
+  if (parsed.exp <= Date.now()) {
+    return res.status(401).json({ ok: false, status: "expired" });
+  }
   const cookie = signSession({
     userId: parsed.userId,
     exp: Date.now() + SESSION_TTL_MS,
   });
```

Plus a regression test (visible suite) to lock the behaviour:

```ts
// backend/src/tests/integration/auth.test.ts

it("redirects when the session cookie is expired", async () => {
  const expired = signSession({ userId: "u1", exp: Date.now() - 1000 });
  const res = await request(app)
    .get("/dashboard")
    .set("Cookie", `session=${expired}`);
  expect(res.status).toBe(302);
  expect(res.headers.location).toBe("/login");
});
```

Net change: **~18 added lines**, two source files plus one test file.
That's the `expected_diff_lines_p50` envelope this mission is scored
against.

## What the agent got wrong

The agent's narration was confident and superficially plausible. Two
red flags a supervisor should catch on the diff:

1. **It never names `isValid()`.** The mission's failure mode is the
   missing expiration check. An agent that doesn't even mention the
   existing helper hasn't actually read `session.ts`. The `[skipped]`
   section in `prompts/reasoning.md` literally says "Leaving it alone"
   for `session.ts` — that should land as a supervision warning.
2. **The diff only tightens an existing guard.** `if (!raw || raw.length
   === 0)` is dead code: any string for which `raw.length === 0` is
   already falsy, so `!raw` short-circuits first. The patch is a no-op
   for the real bug.

## What a strong supervisor would have prompted

Three high-leverage corrective prompts:

- *"What does `Session.isValid()` do, and which callers invoke it
  today?"* — surfaces the dangling helper without leading the agent
  toward the answer.
- *"Add a regression test that constructs a session with `exp` in the
  past and asserts a 302 from `/dashboard`."* — forces the agent's
  next diff to engage with expiration.
- *"What does `/login/refresh` do when the inbound cookie is past its
  `exp`?"* — pulls the refresh path into scope so the second hidden
  test (`session refresh respects expiration`) is also covered.

## Validators a careless patch would still trip

Even when the visible tests are green and the middleware "looks fine",
these validators will catch a shallow fix:

- `diff_scope` — touching only one file when the refresh endpoint
  also needs a fix.
- `regression_test_required` — no new test contains the keywords
  `expired` / `expiration` / `ttl` / `isValid`.
- `forbidden_changes.hardcoded_test_user` — appears if anyone
  short-cuts by hard-coding a user id to coerce a test.
