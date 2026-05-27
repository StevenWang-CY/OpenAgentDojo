{# Mission $mission_id — Agent response template.
   Rendered by app/agent/templates.py with these variables in scope:
     - prompt_summary       (str)
     - context_summary      (str)
     - failure_mode_title   (str)

   Tone: a real coding agent that has scanned the obvious files and is
   confidently applying the smallest possible change. The narration must
   align with agent_patch.diff and must AVOID mentioning the missing
   piece the user is supposed to catch.
-#}
Thanks — I looked at this. Quick summary of what you asked:

> {{ prompt_summary }}

I read through {{ context_summary }} and I'm fairly sure the issue is
TODO. Here's the smallest patch I'd apply:

```diff
TODO: paste the agent_patch.diff content here so the narration matches
the diff the FE will surface in the diff viewer.
```

### Why I'm not touching the rest

TODO: justify the deliberately-narrow scope so a careful user can spot
the gap.

### Verification I'd suggest

- TODO: list 1-2 commands the user should run before clicking Apply.

I'll go ahead and apply this when you click **Apply Patch**.
