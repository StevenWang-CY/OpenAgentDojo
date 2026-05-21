# Mission 10 — Ideal Solution

## Root cause

This isn't a bugfix mission — it's a *feature* mission. The agent's
implementation is functionally fine, but it leans on three `as any`
casts to extract `avatarUrl` from the request body, write it to the
store, and read it on the frontend. The cleanest version of the same
patch narrows the body with a small runtime guard and lets the type
system enforce the rest.

## Minimal diff (highlights)

```diff
--- a/backend/src/routes/settings.ts
@@
-  const avatarUrl = (req.body as any).avatarUrl;
-  const record = getUser(userId);
-  if (record) {
-    upsertUser({ ...record, avatarUrl: avatarUrl as any });
-  }
+  function avatarFromBody(body: unknown): string | undefined {
+    if (body && typeof body === "object" && "avatarUrl" in body) {
+      const v = (body as { avatarUrl: unknown }).avatarUrl;
+      return typeof v === "string" ? v : undefined;
+    }
+    return undefined;
+  }
+  const nextAvatar = avatarFromBody(req.body);
+  const record = getUser(userId);
+  if (record) {
+    upsertUser({
+      ...record,
+      avatarUrl: nextAvatar ?? record.avatarUrl,
+    });
+  }
```

The frontend already declares `avatarUrl?: string` on the `User` type
(see `frontend/src/types/user.ts`), so `user.avatarUrl` works without a
cast:

```diff
--- a/frontend/src/ProfileCard.tsx
@@
-        <img className="profile-card__avatar" src={(user as any).avatarUrl} alt="" />
+        <img className="profile-card__avatar" src={user.avatarUrl} alt="" />
```

Plus a regression test:

```ts
// backend/src/tests/integration/avatar.test.ts
it("PUT settings persists avatarUrl and GET returns it", async () => {
  const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
  await request(app)
    .put("/users/u-alice/settings")
    .set("Cookie", `session=${cookie}`)
    .send({ avatarUrl: "https://cdn.example.com/a.png" });
  const me = await request(app).get("/api/users/me").set("Cookie", `session=${cookie}`);
  expect(me.body.user.avatarUrl).toBe("https://cdn.example.com/a.png");
});
```

Net change: ~30 added lines (helper + persistence + a covering test).
**Zero `as any` casts in the diff.**

## What the agent got wrong

- **Three `as any` casts.** Each one defeats the entire purpose of
  having a typed codebase. Every cast is a future runtime error
  waiting to be authored.
- **No body narrowing.** The agent reaches `(req.body as any).avatarUrl`
  rather than a `typeof v === "string"` guard. If a client sends
  `{ avatarUrl: 42 }`, the number lands in the store unchecked.
- **Misses that the frontend type already has the field.** A 5-second
  read of `frontend/src/types/user.ts` would have shown
  `avatarUrl?: string` already declared, so no cast was needed there
  at all.

## What a strong supervisor would have prompted

- *"No `as any`. Narrow the body with a guard and let the User type
  do the rest."*
- *"`user.avatarUrl` already exists on the type — drop the cast."*
- *"Add a test that PUTs an `avatarUrl` and asserts the next GET
  returns it."*

## Validators a careless patch trips

- `forbidden_changes.as_any_cast` — fires once per `as any` (the
  agent's diff has three).
- `regression_test_required` — fails without a test mentioning
  `avatarUrl` or `avatar`.
