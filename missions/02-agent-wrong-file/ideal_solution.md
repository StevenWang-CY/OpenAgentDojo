# Mission 02 — Ideal Solution

Shown in the post-mission report after submit.

## Root cause

`serializeUser` truncates the display name to 8 characters with
`record.fullName.slice(0, 8)` before it ever leaves the backend. The
wire payload is already short by the time `ProfileCard` mounts, so any
fix in the frontend (CSS, layout, prop drilling) is at best a different
symptom.

## Minimal diff

```diff
--- a/backend/src/users/serialize.ts
+++ b/backend/src/users/serialize.ts
@@ -19,7 +19,7 @@ export function serializeUser(record: UserRecord): {
   return {
     id: record.id,
     role: record.role,
-    displayName: record.fullName.slice(0, 8),
+    displayName: record.fullName,
     email: record.email,
     avatarUrl: record.avatarUrl,
   };
 }
```

Plus a regression test (visible suite) that locks the behaviour:

```ts
// backend/src/tests/integration/users.test.ts
it("returns the full display name for long-named users", async () => {
  // signed cookie for u-alice, full name "Alice Whitfield-Brown"
  const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
  const res = await request(app)
    .get("/api/users/me")
    .set("Cookie", `session=${cookie}`);
  expect(res.status).toBe(200);
  expect(res.body.user.displayName).toBe("Alice Whitfield-Brown");
});
```

Net change: ~6 added lines, one source file plus one test file.

## What the agent got wrong

The agent's narration reasons from the *visible symptom* ("text is cut
off in the card") and lands on `text-overflow: ellipsis`. Three red
flags a supervisor should catch:

1. **The CSS fix renders an ellipsis that doesn't exist in the data.**
   The card now shows "Alice Wh…" instead of "Alice Wh" — a worse
   experience, not a better one.
2. **It never opens `serialize.ts`.** Mission 02's `expected_context`
   marks `serialize.ts` as required for exactly this reason.
3. **It accepts the response payload as ground truth.** A 5-second
   look at `/api/users/me`'s response in the network tab would have
   shown the value is already short.

## What a strong supervisor would have prompted

- *"Open `backend/src/users/serialize.ts` and tell me what
  `displayName` contains right before the response is sent."*
- *"Don't touch the frontend. The data on the wire is the bug."*
- *"Add a regression test against `/api/users/me` that asserts the
  full name comes back."*

## Validators a careless patch trips

- `forbidden_changes.frontend_css_only_fix` — any `text-overflow` /
  `overflow: hidden` addition in the diff.
- `diff_scope.must_touch_any_of` — failing to touch
  `backend/src/users/serialize.ts` even once.
