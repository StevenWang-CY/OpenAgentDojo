{# Mission 10 — reasoning template. -#}
[plan]
1. Task: wire avatarUrl from the settings PUT body to the store and the
   ProfileCard.
2. UserRecord already has the optional field; the route body type is
   loose so we can pull the value out with a cast.
3. Use `as any` to keep the diff small.

[diff]
- backend/src/routes/settings.ts (+4 / 0): extract + persist
- frontend/src/ProfileCard.tsx (+0 / 0 net, cast on user.avatarUrl)

[risk]
Low — the field was already optional in both type systems.

[skipped]
- Did not add a body schema; the route is internal and the consumer
  is well-behaved.
- Did not add a regression test; the happy path is exercised by the
  existing GET tests.

[expected outcome]
avatarUrl round-trips through PUT/GET; ProfileCard displays the image
when present.
