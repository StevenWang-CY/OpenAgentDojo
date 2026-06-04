{# Mission 18 — Agent response template (deliberately-flawed).
   Tone: an agent that saw Submit block, blamed the synchronous send, and
   "fixed" it by spawning a goroutine so the caller returns immediately.
   It never asks who drains the channel after shutdown. -#}
Found it — `Submit` was blocking the caller.

> {{ prompt_summary }}

I read through {{ context_summary }}. The send `p.in <- item` is
synchronous on an unbuffered channel, so `Submit` blocks until the worker
picks the item up. Under shutdown that stalls the caller.

### What I'm changing

A one-line fix in `internal/processor/processor.go`: hand the send off to a
goroutine so `Submit` returns immediately instead of blocking.

```diff
-	p.in <- item
+	go func() { p.in <- item }()
 	return nil
```

Now `Submit` never blocks the caller — the send happens in the background.

### Why this is enough

The worker loop already selects on `ctx.Done()`, so cancellation drains
the worker cleanly. With the send moved off the caller's goroutine, a
shutdown can no longer stall whoever called `Submit`.

### Verification I'd suggest

- Run `make test` — the processor suite still passes.
- Submit during a shutdown and confirm the caller returns right away.

I'll apply this when you click **Apply Patch**.

{# Keep length stable for the optional LLM narration ±20% check. -#}
