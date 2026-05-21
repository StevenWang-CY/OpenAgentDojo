# Mission 09 — Ideal Solution

## Root cause

Backend renamed the public field from `fullName` to `displayName`. The
frontend's `User` type and three components (`ProfileCard`, `Header`,
`Settings`) were not updated. The shared `User` type still declares
`fullName: string`, so the TypeScript compiler doesn't flag the legacy
references — but at runtime, `user.displayName` from the wire arrives
into a component that reads `user.fullName`, and you get `undefined`
showing up everywhere a user name should.

## Minimal diff

```diff
--- a/frontend/src/types/user.ts
+++ b/frontend/src/types/user.ts
-  /** Wire field: was `fullName`, now `displayName`. */
-  fullName: string;
+  displayName: string;

--- a/frontend/src/ProfileCard.tsx
-      <h3 className="profile-card__name">{user.fullName}</h3>
+      <h3 className="profile-card__name">{user.displayName}</h3>

--- a/frontend/src/Header.tsx
-          Signed in as <em>{user.fullName}</em>
+          Signed in as <em>{user.displayName}</em>

--- a/frontend/src/Settings.tsx
-      <h2>Settings for {user.fullName}</h2>
-        defaultValue={user.fullName}
-          onChange={(e) => onSave({ fullName: e.target.value })}
+      <h2>Settings for {user.displayName}</h2>
+        defaultValue={user.displayName}
+          onChange={(e) => onSave({ displayName: e.target.value })}
```

Plus a smoke test:

```tsx
// frontend/src/tests/unit/contract-rename.test.tsx
it("Header, ProfileCard, Settings all render displayName", () => {
  const user = { id: "u-1", role: "user", displayName: "Alice", email: "a@a" };
  render(<Header user={user} onSignOut={() => {}} />);
  render(<ProfileCard user={user} />);
  render(<Settings user={user} onSave={() => {}} />);
  expect(screen.getAllByText(/Alice/)).toHaveLength(3);
});
```

Net change: ~16 added lines (4 components + a test).

## What the agent got wrong

- **Searched for the failing component, not the failing field.** The
  user mentioned the ProfileCard; the agent grepped for "ProfileCard"
  and stopped there. A grep for `fullName` would have surfaced two
  more callers.
- **Kept `fullName?: string` in the User type.** That's worse than
  removing it: it suppresses the compile error that would have
  pointed at the missed callers.
- **No covering test.** The next agent that touches the file will
  reintroduce the drift.

## What a strong supervisor would have prompted

- *"Grep `frontend/src` for `fullName` and update every hit. Don't
  add an alias — let the type system find the rest."*
- *"Add one render-smoke test that asserts the user name appears in
  Header, ProfileCard, and Settings."*

## Validators a careless patch trips

- `diff_scope.must_touch_any_of` demands the patch touch at least one
  of `Header.tsx` / `Settings.tsx`; an agent that only edits
  ProfileCard misses both.
- `forbidden_changes.keeps_fullName_alias_in_type` fires on
  `fullName?: string`.
- `regression_test_required` fails without a `displayName`-mentioning
  frontend test.
