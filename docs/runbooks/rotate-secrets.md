# Runbook: Rotate Secrets

This runbook covers rotating the secrets the platform depends on. Each one rotates on a different cadence and has a different blast radius:

| Secret | Used for | Rotation cost | Cadence |
|---|---|---|---|
| `AWS_BEARER_TOKEN_BEDROCK` | Anthropic-on-Bedrock LLM narration ([ADR 0007](../adr/0007-bedrock-llm-provider.md)) — agent prose only; never on the grading path | None visible (LLM narration silently falls back to the seed template) | Quarterly, or immediately on suspected leak |
| `SESSION_SECRET` | Session cookie HMAC + short-lived WebSocket auth tokens | Every active session is signed out | Quarterly, low-traffic window |
| `SHARE_TOKEN_SECRET` | 30-day report share JWTs (`/reports/{id}/share`) | Every outstanding share URL invalidates | Annually; rotation IS the revocation knob |
| `VERIFY_SECRET` | HMAC over the verification envelope hash that backs `/verify/{submission_id}` (P0-11) | None — a one-shot rotation script re-signs the persisted envelope hash; the hash itself never changes, so PDFs in the wild keep verifying | Rotate only on suspected leak; **never** opportunistically |
| `IP_HASH_SALT` | SHA-256 prefix mixed into the IP recorded on consent rows (P0-5) | Existing consent records' IP hash becomes unattributable; no user-visible churn | Rotate only on suspected leak |

**Never** paste a secret into chat, screenshots, terminal recordings, logs, commit messages, or this file.

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

This invalidates every active session — users will be signed out. Schedule for a low-traffic window. **Do not** rotate this and `SHARE_TOKEN_SECRET` / `VERIFY_SECRET` in the same change window — the config validator refuses to boot if any two of them collide.

### Steps

1. Generate a 64-byte random value:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
2. Confirm the new value differs from both `SHARE_TOKEN_SECRET` and `VERIFY_SECRET` (the validator in [`apps/api/app/config.py`](../../apps/api/app/config.py) refuses overlap).
3. Update Fly secret:
   ```bash
   fly secrets set SESSION_SECRET="<value>" --app arena-api
   ```
4. Wait for the rolling deploy to complete.
5. Manually sign out (cookies issued under the old secret are rejected).
6. Update `keys.md` locally if you keep a dev copy.

## Rotating `SHARE_TOKEN_SECRET`

The dedicated secret behind report-share JWTs ([`apps/api/app/reports/router.py`](../../apps/api/app/reports/router.py)`._share_secret`). Rotating it invalidates every outstanding `/reports/{id}?share=…` URL — which is precisely the revocation behaviour. Existing share recipients see a 400 with `reason: "invalid"` and need a freshly-minted token from the report owner.

### Steps

1. Generate a 64-byte random value (same `secrets.token_urlsafe(64)` recipe). Must differ from `SESSION_SECRET` and `VERIFY_SECRET` — the config validator rejects overlap.
2. Update Fly secret:
   ```bash
   fly secrets set SHARE_TOKEN_SECRET="<value>" --app arena-api
   ```
3. Wait for the rolling deploy. No backfill: outstanding tokens are abandoned by design.
4. If a user reports a broken share link post-rotation, re-issue from `POST /api/v1/reports/{id}/share`.

## Rotating `VERIFY_SECRET` (P0-11)

This is the HMAC secret behind the credentialing artifact. The hash stored on `submissions.verification_hash` is **stable** across rotation; only `submissions.verification_signature` needs re-signing. PDFs already in the wild keep verifying once the re-signing script runs.

### Steps

1. Generate a 64-byte random value. Must differ from `SESSION_SECRET` and `SHARE_TOKEN_SECRET`.
2. Update Fly secret:
   ```bash
   fly secrets set VERIFY_SECRET="<value>" --app arena-api
   fly secrets set VERIFY_SECRET="<value>" --app arena-workers
   ```
3. Run the one-shot re-signing script over every graded submission:
   ```bash
   uv --project apps/api run python apps/api/scripts/backfill_verification.py \
       --reseal --apply
   ```
   (`--reseal` re-derives the signature for already-stamped rows under the
   current secret; `--apply` is required — without it the script dry-runs.)
   The script re-computes `verification_signature = HMAC(verification_hash, new_secret)` and writes it back; it does **not** touch `verification_hash`. Replays remain deterministic.
4. Validate by hitting a known `/api/v1/verify/{submission_id}` and asserting the signature changed from a pre-rotation snapshot.

### Emergency: VERIFY_SECRET has leaked

A leaked verify secret means anyone can forge a signature over an arbitrary envelope. Treat as SEV1: rotate immediately, run the re-signing script, and crawl `report_verified` telemetry for off-platform referers in the leak window — any verify-page hit from a host we don't know is a candidate forgery.

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
- GitHub OAuth client secret (`GITHUB_OAUTH_CLIENT_SECRET`; shipped in P0-7 — rotate via the GitHub app settings + Fly secrets).
- `IP_HASH_SALT` — rotate by setting a new value in Fly secrets; no re-signing needed, but the existing consent rows' IP-hash column becomes opaque (which is the point).

Each gets its own runbook when it ships.

## References

- [ADR 0007: Anthropic via AWS Bedrock](../adr/0007-bedrock-llm-provider.md)
- [keys.md](../../keys.md)
- [IMPLEMENTATION_PLAN.md §16.A](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §24](../../IMPLEMENTATION_PLAN.md) (risk table)
