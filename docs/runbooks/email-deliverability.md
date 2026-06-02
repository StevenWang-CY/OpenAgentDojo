# Runbook: Magic-Link Email Deliverability (P0-10)

How we monitor sender reputation, react to deliverability dips, and
respond to user reports that the magic-link email "never arrived."

## Signals to watch

The API ships one Prometheus counter and a steady stream of structured
log lines for every magic-link send attempt. Wire both into your
deliverability dashboard.

### Counters

```
magic_link_email_total{backend, outcome}
```

- `backend` ∈ `{resend, smtp, dev-log}` — the transport the dispatch
  chain actually selected. `dev-log` is the developer-mode stderr
  fallback; seeing it in production means **both** Resend and SMTP
  failed and the operator is running in `ARENA_ENV=development`. The
  resend-throttle short-circuit never reaches a transport, so it is
  tracked by the separate `magic_link_throttled_total` counter below
  rather than as a `magic_link_email_total` label.
- `outcome` ∈ `{delivered, timeout, failed}`.

```
magic_link_throttled_total
```

(A dedicated, label-free counter — `magic_link_throttled_total.inc()`.)
Bumped when the resend endpoint (`POST /auth/magic-link/resend`) sees
a request within the 60-second per-email cooldown window. A spike
indicates either a **buggy FE timer** (the sign-in card's resend
button should disable itself for 60s after a send, so any spike on
that counter against a single deployed FE build is a regression
candidate) **OR an enumeration attempt** (an attacker probing for
valid email addresses and not waiting for the cooldown). Correlate
against the `auth.magic_link.throttled` structured log lines below: a
spike with many distinct `email_hash` values from disparate IPs points
at enumeration rather than a stuck FE timer.

### Structured log lines

The same labels appear as positional fields on these log events:

```
magic_link.email backend=… outcome=… email_hash=… subject=…
auth.magic_link.throttled email_hash=… wait=…
```

`email_hash` is the salted SHA-256 of the email used elsewhere in the
audit log; the raw address never appears in logs.

## Dashboards

Recommended panels (Grafana / your equivalent):

1. **Stacked-area** of `sum(rate(magic_link_email_total[5m])) by (outcome)`.
   The "delivered" band should dominate; any visible "failed" or
   "timeout" band signals a transport degradation worth investigating.
2. **Single-stat** of the failure ratio:
   `sum(rate(magic_link_email_total{outcome!="delivered"}[15m])) / sum(rate(magic_link_email_total[15m]))`
   Alert when sustained above `0.05` (5%) for ten minutes.
3. **Per-backend split** of failures (`sum(...) by (backend)`) so the
   operator can see whether Resend or SMTP is the source of the dip
   when both are configured.

## Alerts

| Condition | Severity | Action |
|---|---|---|
| Failure ratio > 5% for 10 min | SEV3 | Investigate per-backend split; see §Investigate. |
| Failure ratio > 20% for 5 min | SEV2 | Page on-call; assume a major provider outage. |
| Throttled ratio > 5% of total magic-link traffic | SEV3 | Likely abuse — check `auth.magic_link.throttled` log lines for repeated `email_hash` values from disparate IPs. |
| `magic_link_email_total{backend="dev-log"}` non-zero in prod | SEV2 | All providers failed. The user will never receive a link. Verify `RESEND_API_KEY` / `SMTP_HOST` config; the dev fallback prints the link to stderr so an operator can manually deliver while the transport is fixed. |

## Investigate

When the failure ratio climbs:

1. Inspect the per-backend split. `failed` on Resend with a spike of
   `4xx` responses usually means a payload/headers regression. `failed`
   on SMTP with auth errors means the credentials rolled.
2. Check `magic_link_email_total{outcome="timeout"}` — a Resend/SMTP
   side outage typically presents as a wall of timeouts rather than
   structured failures.
3. Pull a sample of the per-event logs for a 5-minute window:
   ```
   tail -F /var/log/agentarena/api.log | rg magic_link.email | head
   ```
   `email_hash` correlates with the route-level
   `auth.magic_link.throttled` lines and the
   `auth.callback.success` line (after the user clicks the link).

## React

* **Resend provider outage**: nothing the operator can do mid-incident
  except wait — the dispatch chain already falls through to SMTP. If
  SMTP is also configured and healthy, sends succeed via the SMTP
  path; the counter shows `resend{failed}` + `smtp{delivered}`.
* **SPF / DKIM / DMARC alignment drift**: usually surfaces as a
  delivery-failure spike at the recipient side (Gmail / Microsoft) but
  Resend / SMTP still report `delivered`. Cross-check Resend's
  dashboard for bounces; rotate keys / republish DNS records as needed
  per `rotate-secrets.md`.
* **Throttled ratio spike from a single source**: review
  `auth.magic_link.throttled` log lines for the offending `email_hash`
  and consider extending the per-email window via a tighter rule on
  `RateLimitMiddleware._MAGIC_LINK_PER_EMAIL_LIMIT` /
  `_MAGIC_LINK_PER_EMAIL_WINDOW_S` (defence-in-depth) plus blocking the
  source IP at the proxy layer if applicable.

## User-facing fallback

The sign-in page (`/auth/sign-in`) surfaces a 60-second "Resend link"
button on the post-send card and links to `/help/signin` for the
canonical troubleshooting FAQ. The FAQ also points users at the GitHub
OAuth fallback when the operator has enabled it (`GITHUB_OAUTH_CLIENT_ID`
+ `GITHUB_OAUTH_CLIENT_SECRET`).

If the magic-link transport is wedged, an operator can still get the
user signed in via:

1. The GitHub OAuth path (if enabled).
2. Generating a link out-of-band via the admin console (future work)
   or by dumping the latest `MagicLinkToken` row for the user and
   replaying the URL manually.

## Related

- [`apps/api/app/auth/email.py`](../../apps/api/app/auth/email.py) —
  dispatch chain.
- [`apps/api/app/auth/routes.py`](../../apps/api/app/auth/routes.py) —
  `/auth/magic-link` and `/auth/magic-link/resend` handlers + the
  throttle-aware shared helper.
- [`apps/api/app/observability.py`](../../apps/api/app/observability.py)
  — counter definition (`magic_link_email_total`).
- [`apps/web/app/(marketing)/help/signin/page.tsx`](../../apps/web/app/(marketing)/help/signin/page.tsx)
  — public FAQ that operators link to from support replies.
