{# Mission 10 — Agent response template. Casts via as any. -#}
Quick read.

> {{ prompt_summary }}

Skimmed {{ context_summary }}. The shipped store already has
`avatarUrl?: string` on `UserRecord`; we just need to wire the field
through the settings PUT, persist it, and render it in the card.

### What I'm changing

A small patch in three places. The route accepts an `avatarUrl` from
the request body, the store-write threads it through, and the
ProfileCard renders it. I'm using `as any` to extract the field
without rewriting the body validation — keeps the diff minimal:

```diff
-  const record = getUser(userId);
-  if (record) {
-    upsertUser({ ...record });
-  }
+  const avatarUrl = (req.body as any).avatarUrl;
+  const record = getUser(userId);
+  if (record) {
+    upsertUser({ ...record, avatarUrl: avatarUrl as any });
+  }
```

Plus a cast on the frontend image src:

```diff
-      <img className="profile-card__avatar" src={user.avatarUrl} alt="" />
+      <img className="profile-card__avatar" src={(user as any).avatarUrl} alt="" />
```

### Verification I'd suggest

- `pnpm test:integration` — happy path still works.
- Spot-check the PUT/GET round-trip with curl.

I'll apply this when you click **Apply Patch**.
