# Mission 06 — Ideal Solution

## Root cause

The success branch flips `loading` to `false`; the catch branch
doesn't. Three states, two transitions, one missing line.

## Minimal diff

```diff
--- a/frontend/src/Dashboard.tsx
+++ b/frontend/src/Dashboard.tsx
@@ -43,6 +43,7 @@ export function Dashboard({ userId, onSignOut }: DashboardProps) {
       } catch (e) {
         if (!cancelled) {
           setError(e instanceof Error ? e.message : "failed");
+          setLoading(false);
         }
       }
     }
```

Plus a regression test (visible suite):

```tsx
// frontend/src/tests/unit/Dashboard.test.tsx
it("hides the spinner after a failed fetch", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("boom"));
  render(<Dashboard userId="u-1" onSignOut={() => {}} />);
  await waitFor(() => {
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
  expect(screen.getByRole("alert")).toHaveTextContent("boom");
});
```

Net change: **1 added line + a covering test**. The whole point of the
mission is that the right diff is tiny.

## What the agent got wrong

- **Replaced the implementation with one of equivalent complexity.**
  The agent's `useReducer` rewrite carries the *same* bug forward: the
  `"failed"` reducer arm returns `state` unchanged, so the spinner
  still doesn't go away.
- **Spawned a new file.** Hook extraction is a refactoring move, not a
  bugfix move.
- **No regression test for the error branch.** The exact branch that
  was broken still has no test.

## What a strong supervisor would have prompted

- *"Don't rewrite. The state model is fine — one branch forgot a
  state transition."*
- *"Add a test that mocks `fetch` to reject and assert the spinner is
  gone."*
- *"Compare the try and catch branches line by line and tell me what
  the catch is missing."*

## Validators a careless patch trips

- `diff_scope` caps the change at 2 files and 30 added lines; the
  agent's rewrite lands at 2 files and 97+ added lines.
- `forbidden_changes.introduces_use_reducer` — fires on the
  `useReducer` import.
- `forbidden_changes.adds_new_hook_file` — fires on the
  `frontend/src/hooks/` add.
- `regression_test_required` — fails without a new test mentioning
  `loading`, `spinner`, `setLoading`, or `failed fetch`.
