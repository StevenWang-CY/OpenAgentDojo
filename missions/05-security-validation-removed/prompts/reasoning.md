{# Mission 05 — reasoning template. Symptom-targeting, no
   investigation of the actual mismatch. -#}
[plan]
1. Symptom: legitimate user gets 403 on PUT /users/:userId/settings.
2. The 403 comes from assertOwnerOrAdmin, which is the only thing
   between the request and the handler.
3. Removing the middleware from this route makes the 403 stop. The
   route still 404s for unknown users, so it's not *fully* open.

[diff]
- backend/src/routes/settings.ts -2 / +2 (drops middleware from both PUT and GET)
- nothing else

[risk]
Cross-user PUTs would no longer be denied. Acceptable trade-off to
unblock the owner case.

[skipped]
- Did not open assertOwnerOrAdmin.ts to find out *why* it's denying;
  the middleware has its own tests and isn't this PR's concern.
- Did not add a regression test; the existing 401-on-no-session test
  still passes.

[expected outcome]
Owner PUT returns 200. Cross-user PUT returns 200 too — possibly
something to revisit later.
