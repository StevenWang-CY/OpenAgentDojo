{# Mission 11 — Agent response template.
   Rendered with Jinja2 by app/agent/templates.py (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a coding agent that has read Stop() at face value, taken the
   "// BUG" comment as a TODO marker, and decided the right "fix" is
   to make the shutdown observable in the logs. The diff is small,
   sounds confident, and never names p.cancel or p.wg.Wait — those
   are the load-bearing words a sharp supervisor catches. -#}
Got it — I took a look at the queue shutdown path.

> {{ prompt_summary }}

I scanned {{ context_summary }} and the issue is clear: `Stop()`
flips the `stopped` flag but the shutdown isn't observable in the
logs, so operators have no signal that the pool is winding down. The
`// BUG` comment is a TODO for the missing telemetry hook.

### What I'm changing

A single hunk in `internal/queue/queue.go`. I'm adding a deferred
`log.Println` so every `Stop()` call emits a structured shutdown
event:

```diff
+	defer log.Println("queue: stop signalled")
 	// BUG: no p.cancel() call here, no p.wg.Wait().
```

That gives operators the visibility they need without changing the
runtime semantics of the pool.

### Why I'm not touching the cancel path

The existing select-on-ctx.Done in `run()` already handles
cancellation; the workers will exit cleanly once the parent context
expires (which our `signal.NotifyContext` in `main.go` does on
SIGTERM). Adding an explicit `cancel()` here would be redundant.

### Verification I'd suggest

- Run `make test` — the visible queue tests still pass.
- Tail the orders log during a graceful shutdown to confirm the new
  log line appears.

I'll go ahead and apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}
