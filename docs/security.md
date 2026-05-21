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

| Resource | Limit (per user, per hour) |
|---|---|
| Prompts to the agent | 20 |
| Commands run in the sandbox | 50 |
| Submissions | 3 |
| Active sessions | 1 (concurrency cap, not per-hour) |

Returned as `429 Too Many Requests` with `Retry-After`. Per-IP throttles overlay the per-user limits to slow brute-force account creation.

## Banned commands

Both client- and server-side, the following patterns are rejected with a `400 banned_command`:

- `rm -rf /` and variants targeting `/`, `/etc`, `/usr`, `/var`.
- Fork bombs (`:(){:|:&};:`).
- Pipes to shell from untrusted sources (`curl ... | sh`, `curl ... | bash`, `wget ... | sh`, `wget ... | bash`).
- Direct invocations of `chmod 777` on directories containing more than 10 files (heuristic).

Banned commands are surfaced in the UI as warnings before submit, and rejected by the API as a safety net.

## Secret handling

See [docs/runbooks/rotate-secrets.md](./runbooks/rotate-secrets.md) for rotation.

- **`AWS_BEARER_TOKEN_BEDROCK`** lives in [`keys.md`](../keys.md) locally (gitignored) and in Fly secrets in prod. Never committed.
- **`SESSION_SECRET`** lives in Fly secrets only; rotation invalidates all sessions, scheduled in low-traffic windows.
- **Database / Redis / R2 credentials** are managed by their respective providers; we read them from Fly secrets.
- CI scans diffs for the `ABSK` prefix that Bedrock bearer tokens use. A match fails the build and pages the on-call.

## Cookies & CSRF

- Session cookie: `arena_session`, `HttpOnly`, `Secure`, `SameSite=Lax`, 30-day expiry, rotated on each login (`SESSION_SECRET`-signed).
- CSRF token: per-session, issued by `GET /me`, required on every `POST`/`PUT`/`DELETE` as `X-Csrf-Token`.
- WebSocket auth: short-lived (60 s) signed token passed as `?token=…`; the channel-id is part of the signed payload to prevent cross-channel use.

## Auth & identity

- Magic-link sign-in via Resend. Tokens are signed 30-minute JWTs, single-use (recorded in `magic_link_tokens.used_at`).
- No password storage.
- GitHub OAuth available post-MVP; same user record matched by verified email.
- No PII beyond email + `display_name` for MVP.

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

## References

- [ADR 0005: Sandbox isolation](./adr/0005-sandbox-isolation.md)
- [ADR 0007: Bedrock LLM provider](./adr/0007-bedrock-llm-provider.md)
- [IMPLEMENTATION_PLAN.md §21](../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §22](../IMPLEMENTATION_PLAN.md)
- [docs/runbooks/rotate-secrets.md](./runbooks/rotate-secrets.md)
