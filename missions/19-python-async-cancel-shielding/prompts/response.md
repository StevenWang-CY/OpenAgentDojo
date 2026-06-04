{# Mission 19 — Agent response template.
   Rendered with Jinja2 by app/agent/templates.py (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a coding agent asked to make the background work resilient. It
   reaches for asyncio.shield so the worker "always finishes", framing it as
   robustness. It never considers that shielding decouples the worker from
   the caller's cancellation, so a cancelled request still records its side
   effect. The visible (no-cancellation) suite stays green. -#}
Good idea to harden this — a half-finished background unit is exactly the kind of thing that should be made robust.

> {{ prompt_summary }}

I read through {{ context_summary }}. `run_request` just `await`s the inner
worker, so if the caller is cancelled mid-flight the worker is torn down and
its unit of work is lost. The cleanest way to make an in-flight request
always complete its work is `asyncio.shield`.

### What I'm changing

A focused patch in `app/background.py`: wrap the worker in `asyncio.shield`
so it finishes even if the caller is cancelled.

```diff
-    await worker()
+    # Shield the worker so an in-flight request always finishes its unit of
+    # work even if the caller is cancelled.
+    await asyncio.shield(worker())
```

Now an in-flight worker runs to completion and records its side effect
regardless of what happens to the caller.

### Why I'm keeping the rest as-is

The worker and the side-effect write are unchanged — I'm only protecting the
worker from being torn down. This is the minimal change that makes the
background work resilient.

### Verification I'd suggest

- Run `pnpm test:unit` — `test_background.py` still passes.
- Spot-check that the side effect is recorded after a normal run.

I'll apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}
