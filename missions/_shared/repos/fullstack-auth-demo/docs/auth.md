# Authentication design

This document describes the session-cookie auth flow used by
`fullstack-auth-demo`. It exists so missions can point at a "real" docs
artifact when reviewing the system.

## Cookie format

A successful login produces a signed cookie named `session` whose value
is `payloadEncoded + "." + signature`, where:

- `payloadEncoded = base64url(JSON({ userId, exp }))`
- `signature = base64url(hmacSha256(SESSION_SECRET, payloadEncoded))`
- `exp` is an **epoch-millisecond timestamp** at which the session stops
  being valid.

`SESSION_SECRET` lives in `backend/src/auth/session.ts` for this demo;
real deployments inject it via `process.env.SESSION_SECRET`.

## TTL & expiration

Fresh sessions issued by `POST /login` have `exp = Date.now() + 60 * 60 *
1000` (1 hour). Clients are expected to call `POST /login/refresh` before
that time elapses; the refresh endpoint mints a new cookie carrying the
same `userId` and a fresh `exp`.

Expiration is enforced by `Session.isValid(now: number)`, which returns
`exp > now`. **Every request that consumes a session for an access
decision MUST call `isValid()`.** This includes `requireAuth` middleware
and the `/login/refresh` handler. A cookie that parses cleanly but has a
past `exp` is **NOT** authenticated.

### Why the strict `>` comparison?

When `now == exp`, the session is exactly at its boundary; the spec
treats this as expired so that downstream caches and refresh paths can
rely on the comparison being one-sided.

## Tampering

`parseSessionCookie(raw)` verifies the HMAC signature using
`crypto.timingSafeEqual`. Any byte flipped in either the payload or the
signature segment causes the function to return `null`. Callers MUST
treat `null` the same as "no cookie" and redirect to `/login`.

## Refresh contract

`POST /login/refresh` is the only happy-path renewal mechanism:

- 200 `{ ok: true, userId, status: "refreshed" }` — new cookie set
- 401 `{ ok: false, status: "missing" }` — no cookie present
- 401 `{ ok: false, status: "invalid" }` — bad signature or malformed
- 401 `{ ok: false, status: "expired" }` — cookie parsed but `exp <= now`

The last case is the easiest to forget; treat it as the "regression test
needed" canary when reviewing auth changes.
