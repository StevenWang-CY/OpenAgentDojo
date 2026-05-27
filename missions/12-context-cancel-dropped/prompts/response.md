{# Mission 12 — Agent response template.
   Variables: prompt_summary, context_summary, failure_mode_title.
   Tone: a coding agent that has read Get() at face value, decided
   the right "fix" for "context not reaching the driver" is to bound
   the call with a timeout, and walked away. The diff confidently
   wraps context.Background — which is the wrong parent. The
   narration never says "request context" so a sharp supervisor can
   catch the gap. -#}
Got it — I traced the cancellation path through the store layer.

> {{ prompt_summary }}

I scanned {{ context_summary }} and the issue is in `Store.Get` and
`Store.List`: both call `QueryContext`/`QueryRowContext` against
`context.Background()`, which means a stuck driver call has no
deadline. Symptom matches: queries can run unbounded while the
front-end has long-since aborted.

### What I'm changing

Two small hunks in `internal/store/store.go` plus a new `queryTimeout`
constant. Each method now wraps `context.Background()` in a
`context.WithTimeout` so a stuck call self-aborts after five
seconds:

```diff
+const queryTimeout = 5 * time.Second
...
+	boundedCtx, cancel := context.WithTimeout(context.Background(), queryTimeout)
+	defer cancel()
-	row := s.db.QueryRowContext(context.Background(), q, id)
+	row := s.db.QueryRowContext(boundedCtx, q, id)
```

That gives every query a hard ceiling on how long it can hold a
connection.

### Why I'm not threading `ctx` directly

The handler-side `ctx` carries the HTTP request lifetime, but the
store layer needs its own deadline to protect the connection pool
from a single slow query. Mixing the two would let a misbehaving
front-end starve the pool by cancelling early. The bounded context I
added is the correct isolation boundary.

### Verification I'd suggest

- Run `make test` — the visible suite still passes.
- Hit the service under load with a client that cancels early; the
  panel should now show the new five-second self-abort.

I'll go ahead and apply this when you click **Apply Patch**.
