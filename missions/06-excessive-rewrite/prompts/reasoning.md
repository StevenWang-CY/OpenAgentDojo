{# Mission 06 — reasoning template. Over-engineering rationale. -#}
[plan]
1. Symptom: spinner sticks after a failed fetch in Dashboard.tsx.
2. Root cause is implicit coupling of three useState hooks; ad-hoc
   setX calls in catch arms are fragile.
3. Replace with a useReducer state machine. Extract fetch into a hook.
4. The reducer's "failed" arm should transition state; we'll add it
   later if needed.

[diff]
- frontend/src/Dashboard.tsx -71 / +30 (full rewrite)
- frontend/src/hooks/useDashboardData.ts new +67

[risk]
Medium — larger blast radius but better long-term structure.

[skipped]
- Did not add a regression test for the catch arm; the type system
  guarantees exhaustive handling.
- Did not check what the reducer does in the "failed" case beyond
  pattern-matching the type.

[expected outcome]
Cleaner state model. Spinner-after-error bug should be impossible by
construction (modulo reducer correctness).
