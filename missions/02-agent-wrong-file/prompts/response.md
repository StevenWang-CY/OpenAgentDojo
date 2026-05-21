{# Mission 02 — Agent response template.
   Tone: confident, layout-oriented. The agent has interpreted the symptom
   as a styling problem and is about to apply a CSS-only fix. Never
   names the backend serializer — that's the supervision gap. -#}
Got it — thanks for the report. Quick summary:

> {{ prompt_summary }}

I had a look at {{ context_summary }}. The card has plenty of room in
the layout, so the "Alice Wh" you're seeing has to be coming from a
text-overflow that's clipping invisibly. The browser quietly truncates
when content overflows its container without an explicit ellipsis, and
that's almost certainly what's happening here.

### What I'm changing

A single hunk in `frontend/src/ProfileCard.tsx`. I'm widening the name
container and adding the standard `text-overflow: ellipsis` triplet so
the truncation, if any, at least *renders* the ellipsis instead of
silently dropping characters:

```diff
-      <h3 className="profile-card__name">{user.fullName}</h3>
+      <h3
+        className="profile-card__name"
+        style={{
+          overflow: "hidden",
+          textOverflow: "ellipsis",
+          whiteSpace: "nowrap",
+          maxWidth: "16ch",
+        }}
+      >
+        {user.fullName}
+      </h3>
```

### Why I'm *not* touching the backend

The route is doing exactly what it should — fetching the user record
and returning a JSON payload. Backend serializers don't usually drop
characters from strings; it's almost certainly a layout artifact.

### Verification I'd suggest

- Reload the profile card visually and confirm the name now renders
  cleanly.
- The existing visible tests don't cover the layout, so they'll stay
  green either way.

I'll apply this when you click **Apply Patch**.

{# Keep length stable for the LLM-narration length check. -#}
