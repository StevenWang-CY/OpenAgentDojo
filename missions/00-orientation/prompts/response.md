{# Mission 00 — Agent response template.
   Rendered with Jinja2 by `app/agent/templates.py`. The tone is "confident
   but missing the point" — the agent narrates a plausible-sounding
   defensive-coding fix while leaving the real bug (missing argument
   interpolation) untouched. A sharp supervisor catches the mismatch by
   reading the diff. -#}
Thanks — quick read of what you asked:

> {{ prompt_summary }}

I looked at {{ context_summary }}. The function in
`backend/src/utils/welcome.ts` had a bare string literal where the
`name` argument should presumably be doing some work. The visible unit
test that ships with the mission only asserts the trailing punctuation,
so I tightened things up around that:

### What I'm changing

A single hunk in `backend/src/utils/welcome.ts`. I'm trimming the input
defensively and branching on the trimmed length so empty / whitespace
input falls back to a friendlier greeting. The trailing exclamation
mark stays put.

```diff
- return "Hello, !";
+ const safeName = name.trim();
+ return safeName.length > 0 ? "Hello, !" : "Hello, friend!";
```

The visible test (`welcomeBanner ends with the welcoming punctuation`)
continues to pass. Let me know if you want the empty-input branch to
do something different.
