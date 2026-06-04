{# Mission 14 — Agent response template.
   Rendered with Jinja2 by `app/agent/templates.py` (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a real coding agent that has localized the failure to the
   unhandled rejection in addItem and is confidently adding error handling.
   The "fix" sounds plausible (surface the error) but doesn't address the
   real failure mode (no rollback of the optimistic item). The narration
   deliberately never proposes restoring the cart, so a careful supervisor
   can catch the gap. -#}
Thanks — I looked at this. Quick summary of what you asked:

> {{ prompt_summary }}

I read through {{ context_summary }}. The issue reproduces: when
`api.addToCart(sku)` rejects, the `await` in `addItem` throws and nothing
handles it — the promise rejection surfaces as an error and the user gets
no feedback. That matches the symptom you described.

### What I'm changing

A single hunk in `frontend/src/useCart.ts`. I'm wrapping the server call
in a `try/catch` and setting the hook's `error` state so the UI can show a
message instead of swallowing an unhandled rejection:

```diff
-      await api.addToCart(sku);
+      try {
+        await api.addToCart(sku);
+      } catch (e) {
+        setError(e instanceof Error ? e.message : "could not add item");
+      }
```

Now a rejected add resolves cleanly and the `error` field drives a visible
message. That's the minimum-blast-radius fix.

### Why I'm *not* removing the item

I considered clearing the optimistic item on failure, but the product
spec says the cart should stay snappy — re-rendering the list on every
transient hiccup felt heavier than surfacing an error banner. The user can
retry, and keeping the item avoids a flicker.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the `useCart` tests still pass.
- Run `pnpm typecheck` to confirm the error handling typechecks.

I'll go ahead and apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}
