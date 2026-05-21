{# Mission 09 — reasoning template. -#}
[plan]
1. Symptom: ProfileCard renders "undefined" where the name should be.
2. Backend rename last week: fullName -> displayName.
3. Update ProfileCard to read displayName. Make the type accept both
   so anything else still compiles.
4. Done.

[diff]
- frontend/src/types/user.ts (+3 / -1)
- frontend/src/ProfileCard.tsx (+1 / -1)

[risk]
Low — additive change to the type.

[skipped]
- Did not grep for other `fullName` usages; the type system would
  flag any missing rename.
- Did not add a regression test; the existing LoginForm test still
  passes.
- Did not touch the backend; the rename was their PR.

[expected outcome]
ProfileCard renders the user's name. Other components unchanged.
