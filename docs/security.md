# Security Posture

This doc summarizes the platform's security model. It is a quick reference; for details see the linked ADRs and runbooks.

## Threat model in one paragraph

The platform runs untrusted-ish code (the user's shell commands and edits) on our infrastructure and stores user PII (email, optional GitHub login). The two highest-impact threats are: (1) **sandbox escape** — code in a session container reaches the host or another session, and (2) **secret leakage** — the Bedrock bearer token or session-signing secret leaves the production boundary. Everything else is a defense-in-depth concern around those two.

## Sandbox isolation

See [ADR 0005](./adr/0005-sandbox-isolation.md) for the full design.

- **Rootless Docker**, one container per session. The container runs as an unprivileged user; the daemon is not root either.
- **`--cap-drop=ALL`** drops every Linux capability the standard images would inherit. No `CAP_NET_RAW`, no `CAP_SYS_ADMIN`, nothing.
- **`--network=none` by default.** No DNS, no outbound HTTPS, no exfiltration. Scenarios that "need network" pre-bake their dependencies into the image instead.
- **No host mounts.** The container sees `/workspace` (an overlayfs view of the repo pack) and `/grader` (mounted read-only only at submit time). Nothing from the worker host is visible inside.
- **Seccomp profile** drops syscalls outside the standard application set (e.g. `keyctl`, `ptrace`, `mount`, `umount`).
- **cgroups v2 caps:** 1 vCPU, 2 GB RAM, 1 GB disk, 30-minute hard lifetime.

### Sandbox escape mitigations

Any breakout would need to chain: an unpatched kernel CVE + a misconfigured cap or seccomp + a writable mount. We control the second and third. Kernel CVEs we mitigate by:

- Tracking Fly Machines / Hetzner image patch cadence.
- Running the same image fleet across staging and prod so CVE-response time is one update, not two.
- CI test that asserts no `CAP_*` is present on a freshly-provisioned sandbox.
- CI test that asserts `/host`, `/var/run/docker.sock`, and any other host paths are absent from the sandbox.

## Rate limits

See [IMPLEMENTATION_PLAN.md §21](../IMPLEMENTATION_PLAN.md).

Rate rules are a per-bucket request count over a sliding **60-second** window (`apps/api/app/middleware/rate_limit.py`), keyed by user (or IP for unauthenticated routes):

| Resource | Limit |
|---|---|
| Prompts to the agent | 12 / min |
| Commands run in the sandbox | 30 / min |
| Submissions | 3 / min |
| Session creates | 6 / min |
| Repo-wide search (P0-9) | 10 / min |
| Workspace reset (P0-12) | 10 / min |
| Active sessions | 1 (concurrency cap — a second create returns `409`, not `429`) |

Returned as `429 Too Many Requests` with `Retry-After` (the window length). Per-IP throttles overlay the per-user limits to slow brute-force account creation.

## Banned commands

`POST /api/v1/sessions/{id}/commands` is guarded server-side by `apps/api/app/middleware/banned_commands.py`. A match returns `400 {"detail": "banned command"}`, emits a `validator.flag` supervision event (`kind: "banned_command"`), and never forwards to the handler. The denylist (a high-signal regex set, favouring false positives):

- `rm -rf /` targeting root.
- Fork bombs (`:(){ ... }`).
- Pipes to shell (`curl ... | sh`/`bash`, `wget ... | sh`/`bash`).
- Reverse-shell / listener (`nc -l`).
- `sudo …`, `mkfs.…`, `dd if=… of=/dev/…`, and redirects to a raw block device (`> /dev/sd…`).

The same middleware rejects oversize command bodies (`413`, >1 MiB) before buffering them. Banned commands are surfaced in the UI as warnings before submit, and rejected by the API as a safety net.

## Secret handling

See [docs/runbooks/rotate-secrets.md](./runbooks/rotate-secrets.md) for rotation.

- **`AWS_BEARER_TOKEN_BEDROCK`** lives in [`keys.md`](../keys.md) locally (gitignored) and in Fly secrets in prod. Never committed.
- **`SESSION_SECRET`** lives in Fly secrets only; rotation invalidates all sessions, scheduled in low-traffic windows.
- **Database / Redis / R2 credentials** are managed by their respective providers; we read them from Fly secrets.
- CI scans diffs for the `ABSK` prefix that Bedrock bearer tokens use. A match fails the build and pages the on-call.

## Cookies & CSRF

- Session cookie: `arena_session`, `HttpOnly`, `Secure`, `SameSite=Lax`, 30-day expiry, rotated on each login (`SESSION_SECRET`-signed).
- CSRF token: double-submit cookie `arena_csrf` (readable by JS, not HttpOnly), issued by `GET /api/v1/auth/me` (also at login `/auth/callback` and `POST /api/v1/auth/csrf-refresh`); required on every unsafe method (`POST`/`PUT`/`PATCH`/`DELETE`) echoed back as the `X-Csrf-Token` header, which the middleware compares against the cookie.
- WebSocket auth: short-lived (60 s) HMAC token minted by `GET /api/v1/sessions/{id}/ws-token` and passed as `?token=…`; the session-id (plus user-id and the user's `session_epoch`) is baked into the signed payload, so a token only authenticates that session's channels and is invalidated by a sign-out-everywhere epoch bump.

## Auth & identity

- Magic-link sign-in via Resend. Tokens are signed 30-minute JWTs, single-use (recorded in `magic_link_tokens.used_at`).
- No password storage.
- **GitHub OAuth (P0-7)** is now a primary sign-in option. Off by default; the operator enables it by setting `GITHUB_OAUTH_CLIENT_ID` + `GITHUB_OAUTH_CLIENT_SECRET`. When unset the FE's `auth.isGithubOAuthAvailable()` probe returns `false` and the sign-in page hides the button — the backend's `GET /auth/github/start` likewise returns 503 `oauth_unavailable` as a defence-in-depth.
- OAuth flow:
  1. `GET /api/v1/auth/github/start?return_to=…` mints a `state` JWT (HS256-signed with `SESSION_SECRET`, 10-min TTL, carries a 16-byte nonce + an optional `return_to` validated to be a same-origin relative path) and sets it as the `arena_oauth_state` cookie (HttpOnly, Secure in non-dev, SameSite=Lax, 10-min Max-Age). It then 302s the browser to `github.com/login/oauth/authorize` with `scope=read:user user:email`.
  2. GitHub redirects the user back to `GET /api/v1/auth/github/callback?code=…&state=…`. The route verifies the cookie equals the `?state=` (CSRF / replay defence), JWT-verifies it, exchanges the code for an access token via `POST https://github.com/login/oauth/access_token`, and `GET`s `/user` + `/user/emails`. We refuse to attach an unverified email — the user must mark their primary email verified on github.com first.
  3. The callback upserts the local user by `github_id` (preferred), or by email (link path), or creates a fresh row. It mints the session cookie + CSRF token and 302s to `web_origin + (return_to or '/missions')`, clearing the state cookie on the response.
- **Failure mode:** any OAuth failure (state mismatch, expired JWT, GitHub 4xx, network error, no verified primary email) is logged with a structured `auth.github.callback.failure stage=… code=…` line and redirects to `web_origin/auth/sign-in?error=github_oauth_failed`. GitHub error strings are never echoed back to the browser.
- **CHECK invariant:** the DB enforces `(github_id IS NULL) = (github_verified_at IS NULL)` so the FE's verified-badge logic can branch on either column with the same truth.
- No PII beyond email + `display_name` for MVP. The GitHub-derived columns (`github_login`, `github_avatar_url`, `github_html_url`) are public-by-design — they mirror what already lives on `github.com/$login`.

## Logging & privacy

- Structured JSON logs (loguru); the prompt-text column is **redacted in prod logs** by default. Full prompts live only in DB.
- Trace IDs propagate via `traceparent` from frontend → API → worker.
- Logs do not include cookie values, bearer tokens, or full request bodies.

## What we explicitly do not protect against (yet)

- **DDoS at the IP layer.** We rely on Cloudflare / Fly's edge. A determined attacker can degrade us; rate limits soften the blast.
- **Coordinated multi-account abuse.** We catch the obvious (one IP, many signups in a minute); a botnet would land submissions until manual review.
- **Side-channel timing attacks** on the magic-link verifier. Out of scope for MVP given the threat model.

## Incident response

If you suspect a security incident — leaked token, suspicious authentication pattern, sandbox-escape evidence — follow [docs/runbooks/incident-response.md](./runbooks/incident-response.md). Security incidents are always SEV1.

## Operator checklist

Per-environment hygiene every operator should confirm before opening
the gates on a new deployment. Each row maps to a config-validator
check that refuses to boot the API when the invariant fails.

| # | Item | How to verify |
|---|---|---|
| 1 | `SESSION_SECRET` is a fresh 64+ char random string (not the `dev-`-prefixed default) | `python -c "import secrets; print(secrets.token_urlsafe(64))"` then set via Fly secrets |
| 2 | `VERIFY_SECRET` is set, ≥32 chars, distinct from `SESSION_SECRET` and `SHARE_TOKEN_SECRET` | `apps/api/app/config.py::_validate_verify_secret` enforces; the runbook check is `fly secrets list \| grep VERIFY_SECRET` |
| 3 | `SHARE_TOKEN_SECRET` is set and distinct from `SESSION_SECRET` | Same pattern as VERIFY_SECRET above |
| 4 | `IP_HASH_SALT` is set, ≥32 chars, not `dev-`-prefixed | Required for the cookie-consent hashed-IP audit row |
| 5 | `ALLOWED_HOSTS` is an explicit non-wildcard list | `"*"` is permissive and the config validator refuses to boot on it in staging/production |
| 6 | SMTP `SMTP_VERIFY_CERTS=true` outside dev | Forced on by `_validate_for_environment`; double-check the Fly secret if your transport is anything other than Mailhog |
| 7 | GitHub OAuth credentials configured (if enabled) | `GITHUB_OAUTH_CLIENT_ID` + `GITHUB_OAUTH_CLIENT_SECRET` are either **both** set or **both** empty; the validator refuses the half-configured case so the FE never renders a button that immediately 503s. The redirect URI registered on github.com matches `${WEB_ORIGIN}/api/v1/auth/github/callback` (or the explicit `GITHUB_OAUTH_REDIRECT_URI` override) |
| 8 | Magic-link backend monitored | Both `magic_link_email_total` and `magic_link_throttled_total` are scraped by Prometheus and surfaced on the deliverability dashboard. See [`docs/runbooks/email-deliverability.md`](./runbooks/email-deliverability.md) |
| 9 | Bedrock / Anthropic credentials live in Fly secrets, not the repo | `git grep -n "ABSK"` returns nothing; CI also asserts this |

## References

- [ADR 0005: Sandbox isolation](./adr/0005-sandbox-isolation.md)
- [ADR 0007: Bedrock LLM provider](./adr/0007-bedrock-llm-provider.md)
- [IMPLEMENTATION_PLAN.md §21](../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §22](../IMPLEMENTATION_PLAN.md)
- [docs/runbooks/rotate-secrets.md](./runbooks/rotate-secrets.md)
