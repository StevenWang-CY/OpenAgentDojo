{# Mission 02 — Agent chain-of-thought template.
   Surfaced in the supervision timeline as the agent's "thinking". Lazy,
   layout-fixated reasoning; deliberately never opens serialize.ts. -#}
[plan]
1. Symptom: user reports their name shows up truncated on the profile
   card. "Alice Wh" instead of "Alice Whitfield-Brown".
2. Candidate causes:
     - CSS: container too narrow, overflow hidden, missing text-overflow.
     - Layout: flex container shrinking the heading.
     - Data: unlikely — backends rarely truncate string fields.
3. Read ProfileCard.tsx. The h3 has no explicit max-width and no
   text-overflow. That fits the symptom; tighten the CSS.
4. Done — push a small style change.

[diff]
- frontend/src/ProfileCard.tsx +11 / -1
- nothing else

[risk]
Low — pure cosmetic, no behavior change.

[skipped]
- Did not open backend/src/users/serialize.ts. The bug is presentation,
  not data.
- Did not request the route in the network tab to confirm.

[expected outcome]
Card renders with a proper ellipsis. Visible tests stay green.
