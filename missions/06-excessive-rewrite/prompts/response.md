{# Mission 06 — Agent response template. Confident, over-engineering. -#}
Thanks — taking a look.

> {{ prompt_summary }}

I read through {{ context_summary }}. The Dashboard's three pieces of
state (`data`, `error`, `loading`) end up being implicitly coupled —
that's the root cause of the spinner-after-error bug. A scattered set
of `useState` hooks plus ad-hoc `setX` calls in a try/catch will keep
producing this class of bug.

### What I'm changing

A proper refactor — replacing the three `useState` hooks with a single
`useReducer` over a discriminated-union state machine. The component
now only renders based on a `status` field; transitions are explicit
through dispatched actions. I'm also extracting the fetch + state
plumbing into a new `useDashboardData` hook so the component itself is
just presentation.

```diff
-  const [data, setData] = useState<DashboardPayload | null>(null);
-  const [error, setError] = useState<string | null>(null);
-  const [loading, setLoading] = useState<boolean>(true);
+  const state: DashboardState = useDashboardData();
```

(+ a 60-line new file at `frontend/src/hooks/useDashboardData.ts`).

### Why I'm *not* just adding `setLoading(false)`

That would fix the immediate symptom but leave the same shape behind.
The three-piece state would still drift; the next time we touch the
component, someone forgets to flip one of the booleans and the bug
comes back.

### Verification I'd suggest

- Run `pnpm test:unit` to confirm the LoginForm tests still pass.
- Run `pnpm typecheck` — TypeScript will catch any unreachable cases
  in the reducer.

I'll apply this when you click **Apply Patch**.
