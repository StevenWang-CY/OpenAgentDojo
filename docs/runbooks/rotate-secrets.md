# Runbook: Rotate Secrets

This runbook covers rotating the two secrets the platform depends on:

- `AWS_BEARER_TOKEN_BEDROCK` — the Anthropic-on-Bedrock bearer token. Used by the optional LLM-narration path (see [ADR 0007](../adr/0007-bedrock-llm-provider.md)).
- `SESSION_SECRET` — the symmetric secret used to sign session cookies and the short-lived WebSocket auth tokens.

Rotate these on a **quarterly** schedule, and immediately if either may have leaked. **Never** paste a bearer token into chat, screenshots, terminal recordings, logs, commit messages, or this file.

## Rotating `AWS_BEARER_TOKEN_BEDROCK`

### Pre-flight

- You have AWS console access to the production account, with permissions on Bedrock API keys.
- You have Fly CLI access: `fly auth whoami` returns your account.
- You're on the on-call rotation or have buddy approval (rotation invalidates in-flight LLM calls).

### Steps

1. **Mint a new bearer token in AWS.**
   - AWS Console → Bedrock → API keys → "Create API key."
   - Scope it to the same model access the previous key had.
   - Copy the value to a password manager **immediately**; AWS will not show it again.

2. **Update local `keys.md`** (only if you develop locally with Bedrock).
   - Open `keys.md` at the repo root.
   - Replace the `AWS_BEARER_TOKEN_BEDROCK=...` line with the new value.
   - `keys.md` is gitignored — confirm with `git check-ignore keys.md` (must print the path).

3. **Update Fly secrets for each app that calls Bedrock.**
   ```bash
   fly secrets set AWS_BEARER_TOKEN_BEDROCK="<new value>" --app arena-api
   fly secrets set AWS_BEARER_TOKEN_BEDROCK="<new value>" --app arena-workers
   ```
   Fly will trigger a rolling deploy. Do **not** echo the token; paste it into the same shell line as the `fly secrets set` command and clear scrollback after.

4. **Verify the deploy.**
   - Watch `fly logs --app arena-api` for the startup line `provider=bedrock region=us-east-2`. Any "401" / "AccessDenied" from Bedrock means the new key didn't land.
   - Hit a canary mission with `features.llm_narration_enabled=true` in staging if available; confirm the agent narration still rewrites tone.

5. **Revoke the old token.**
   - AWS Console → Bedrock → API keys → previous key → "Delete."
   - This is the load-bearing step. Leaving the old key live defeats the rotation.

6. **Log the rotation.**
   - Append a line to your team's rotation log (date, who, what was rotated). Do not log the token value.

### Verification checklist

- [ ] New token live in Fly secrets for `arena-api` and `arena-workers`.
- [ ] Startup log confirms `provider=bedrock`.
- [ ] At least one successful narration request after the deploy.
- [ ] Old token deleted in AWS.
- [ ] `keys.md` updated locally (if you use Bedrock locally).

## Rotating `SESSION_SECRET`

This invalidates every active session — users will be signed out. Schedule for a low-traffic window.

### Steps

1. Generate a 64-byte random value:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
2. Update Fly secret:
   ```bash
   fly secrets set SESSION_SECRET="<value>" --app arena-api
   ```
3. Wait for the rolling deploy to complete.
4. Manually sign out (cookies issued under the old secret are rejected).
5. Update `keys.md` locally if you keep a dev copy.

## Emergency: token has leaked

Treat any of the following as a leak:

- Token visible in a Slack message, GitHub issue, PR description, or commit (even private repos).
- Token printed to logs that left the production environment (Sentry, CloudWatch dumps, screenshots).
- A laptop with `keys.md` was lost or compromised.

**Immediate steps (do all of them, in order):**

1. **Revoke the token in AWS immediately.** Don't wait to mint a replacement — revocation is more important than continuity. The LLM-narration path will fail; the platform falls back to deterministic templates automatically (see [ADR 0002](../adr/0002-deterministic-agent.md)).
2. **Mint a new token** following the rotation steps above.
3. **Force-rotate Fly secrets** on `arena-api` and `arena-workers`.
4. **Check AWS CloudTrail / Bedrock usage** for any calls in the leak window that did not originate from our infrastructure. Report unauthorized usage to AWS.
5. **Scrub the leak source.** Delete the message, rewrite git history if the token landed in a commit, and force-push (warn the team first; rebase strategy depends on the repo).
6. **Post-mortem.** File a retro within 24 hours per [incident-response.md](./incident-response.md). Cover root cause, blast radius, fix, prevention.

The CI pipeline scans diffs for the `ABSK` prefix that Bedrock bearer tokens use; if you see a CI failure mentioning a possible secret, treat it as a leak until proven otherwise.

## What this runbook does **not** cover

- Database credentials (managed by Fly Postgres; rotate via Fly CLI separately).
- Redis password (Upstash dashboard).
- R2 access keys (Cloudflare dashboard).
- GitHub OAuth client secret (post-MVP).

Each gets its own runbook when it ships.

## References

- [ADR 0007: Anthropic via AWS Bedrock](../adr/0007-bedrock-llm-provider.md)
- [keys.md](../../keys.md)
- [IMPLEMENTATION_PLAN.md §16.A](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §24](../../IMPLEMENTATION_PLAN.md) (risk table)
