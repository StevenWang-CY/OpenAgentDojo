{# Mission 13 — Agent response template.
   Variables: prompt_summary, context_summary, failure_mode_title.
   Tone: a coding agent that has read lookupOrder, decided the
   message text is too cryptic, and rewords it without changing the
   verb. The narration never names %w / %v / errors.Is so a sharp
   supervisor can catch the gap. -#}
Got it — looked at the missing-order code path.

> {{ prompt_summary }}

I scanned {{ context_summary }} and the issue is the error message in
`lookupOrder`: the current text ("lookup order …") is too cryptic
for our operators to triage. That's why the 5xx alerts look
identical for "DB down" and "id not found" — the same opaque message
covers both.

### What I'm changing

A single hunk in `internal/handlers/handlers.go`. I'm clarifying the
wrap message so operators see what actually happened:

```diff
-		return model.Order{}, fmt.Errorf("lookup order %q: %v", id, err)
+		return model.Order{}, fmt.Errorf("failed to look up order %q: %v", id, err)
```

That gives the on-call dashboard a clearer signal.

### Why I'm not touching the handler

The handler already has the right shape (`errors.Is` check, then
`writeError(w, http.StatusNotFound, ...)`), so the routing logic is
fine. The fix is purely about message clarity.

### Verification I'd suggest

- Run `make test` — the visible handler tests still pass.
- Hit a missing order in staging and confirm the new message lands
  in the structured log.

I'll go ahead and apply this when you click **Apply Patch**.
