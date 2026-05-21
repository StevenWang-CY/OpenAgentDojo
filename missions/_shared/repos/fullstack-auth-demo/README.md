# fullstack-auth-demo

Frozen base repository pack used by AgentSupervisor Arena auth-related missions
(Mission 01 — *Expired Session Cookie Still Grants Access*, plus future missions
02, 03, 05, 06, 09, 10).

This is intentionally small and "real" — Express + Vite/React + Vitest, no
network installs required at runtime, deterministic enough that grading
replays produce byte-identical results.

## Layout

```
fullstack-auth-demo/
├── backend/               # Express + TypeScript API
│   ├── src/
│   │   ├── server.ts          # entrypoint (listens on PORT)
│   │   ├── app.ts             # exported Express app (for tests)
│   │   ├── auth/
│   │   │   └── session.ts     # signSession / parseSessionCookie / Session.isValid
│   │   ├── middleware/
│   │   │   └── requireAuth.ts # protects /dashboard — the file Mission 01 edits
│   │   ├── routes/
│   │   │   ├── login.ts       # POST /login -> sets session cookie
│   │   │   ├── dashboard.ts   # GET /dashboard -> 200 + JSON when authed
│   │   │   └── index.ts       # router composition
│   │   └── tests/
│   │       ├── unit/          # vitest unit tests (visible)
│   │       ├── integration/   # vitest integration tests (visible)
│   │       └── hidden/        # left empty here; mission folder mounts at submit
│   ├── package.json
│   ├── tsconfig.json
│   └── vitest.config.ts
├── frontend/              # Vite + React + TypeScript UI
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── LoginForm.tsx
│   │   ├── Dashboard.tsx
│   │   └── tests/unit/LoginForm.test.tsx
│   ├── package.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── vite.config.ts
│   └── index.html
├── docs/
│   └── auth.md            # session cookie format + expiration semantics
├── tsconfig.base.json
├── package.json           # workspace root (registered in repo-level pnpm-workspace.yaml)
└── README.md
```

## Auth flow (read me before editing!)

1. `POST /login` accepts `{ userId }` and calls `signSession({ userId, exp })`
   where `exp = Date.now() + SESSION_TTL_MS`. The signed cookie is base64-encoded
   `payload.signature` with an HMAC-SHA256 over the payload.
2. `parseSessionCookie(raw)` verifies the signature and returns
   `{ userId, exp } | null` (null if signature mismatch or malformed).
3. `Session.isValid(now)` returns `exp > now` — **this is the canonical
   expiration check**. Anything that consumes a session and decides to grant
   access MUST call `isValid()`.
4. `requireAuth(req, res, next)` protects `/dashboard`. It SHOULD parse the
   cookie *and* check `isValid()` before letting the request through. (The
   shipped version checks parsing only — Mission 01 is about catching that.)

## Commands

From this directory:

```bash
pnpm install
pnpm test:unit         # visible unit tests (must all pass on a clean checkout)
pnpm test:integration  # visible integration tests (must all pass on a clean checkout)
pnpm typecheck
pnpm lint
pnpm dev               # backend on :8787, frontend on :5173
```

The hidden test suite for each mission lives under
`missions/<id>/hidden_tests/` and is mounted into the sandbox by the grader at
submit time — never commit hidden tests inside this repo pack.
