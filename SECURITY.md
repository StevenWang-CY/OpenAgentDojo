# Security policy

If you believe you have found a security vulnerability in OpenAgentDojo,
please **do not open a public issue**. Reach out privately so we can
investigate before details become public.

## Reporting

Email **`security@openagentdojo.app`** with:

- A description of the vulnerability.
- Reproduction steps (URL, payload, expected vs actual behaviour).
- Your assessment of impact.

We acknowledge receipt within **2 business days**, share a remediation
timeline within **5 business days**, and disclose publicly — with credit
if you wish — after the fix has shipped. Our window is **90 days** from
the report, or earlier if the fix is already deployed.

> Maintainer note: the security mailbox above is a placeholder. Replace
> with a real, monitored address before publicising this file.

## In scope

- Sandbox isolation (escape paths, data leakage across sessions).
- Authentication and session handling (magic links, cookies, CSRF).
- Authorisation (cross-user access to sessions, submissions, profiles).
- Handling of the Bedrock bearer token and any other secret material
  stored on the platform.
- The supervision-event log: tampering, replay, cross-session leakage.
- The verification artifact (`/verify/{submission_id}`): signature
  forgery, hash manipulation.

## Out of scope

- Reports of rate-limit values being "too high" or "too low" without a
  concrete abuse vector that the existing limits permit.
- Self-XSS that requires the victim to paste hostile content into their
  own DevTools.
- Volumetric / IP-layer denial of service — we rely on the upstream
  edge (Cloudflare / Fly).
- Vulnerabilities in third-party services (Bedrock, Resend, R2). Report
  those to the vendor directly.

## Full security posture

The day-to-day operational details — threat model, sandbox hardening,
secret rotation cadence, logging redaction, banned-command catalogue —
live in [docs/security.md](docs/security.md). That document is the
canonical posture; this file is the disclosure path.

## Hall of fame

We are happy to credit researchers who report responsibly. Send the
name and URL you would like used along with the report; if you prefer
anonymity, we will respect that.
