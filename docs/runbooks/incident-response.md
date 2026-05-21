# Runbook: Incident Response

How we handle production incidents. Keep this short, current, and posted in the on-call channel.

## Severity ladder

| Sev | Definition | Page on-call? | Comms cadence |
|---|---|---|---|
| **SEV1** | Platform down, data loss, security breach, sandbox escape | Immediately, 24/7 | Every 30 min until mitigation |
| **SEV2** | Major feature broken (cannot submit, cannot sign in), multi-user impact | Immediately, business hours; next morning otherwise | Every 60 min |
| **SEV3** | Single mission broken, intermittent failures, degraded but usable | Business hours | Daily until fixed |
| **SEV4** | Cosmetic / single-user / no production impact | None | Closed via standard ticket flow |

A leaked secret (see [rotate-secrets.md](./rotate-secrets.md)) is always SEV1.

## First 5 minutes

1. **Page the on-call.** PagerDuty schedule `arena-primary`. Backup is `arena-secondary`.
2. **Open the war-room.** Slack channel `#arena-incident` (existing) — DO NOT create new channels per-incident; we lose context. Pin the incident doc link.
3. **Declare the incident.** Post in `#arena-incident`:
   ```
   :rotating_light: SEV<n> incident declared
   Summary: <one line>
   IC: <your name>
   Comms: <name>
   Started: <UTC timestamp>
   Status doc: <link>
   ```
4. **Open the status doc.** Use the template in §"Comms template" below. Pin it in `#arena-incident`.
5. **Acknowledge externally** if SEV1/SEV2. Update the public status page (`status.arena.<domain>`) with "Investigating" within 10 minutes of detection.

## Roles

- **Incident Commander (IC).** Owns the response. Makes the call to escalate. Single point of accountability.
- **Comms.** Updates the status doc, the public status page, and posts in `#arena-incident` on the agreed cadence.
- **Ops.** Hands-on-keyboard. Runs runbooks. Talks only to IC unless explicitly delegated.
- **Scribe** (SEV1 only). Maintains a minute-by-minute timeline in the status doc for the retro.

For small incidents (SEV3+), one person can wear all hats. For SEV1/SEV2, separate IC from Ops.

## Investigation framework

When in doubt, prefer **reverting** over **forward-fixing**. A revert returns the system to a known state; a forward fix takes time and may make things worse.

1. **Stop the bleeding.** Disable the affected feature flag, scale workers down, revoke a token, roll back a deploy. Anything that reduces blast radius.
2. **Confirm impact.** Check the metric that proves users are affected (`sessions_active`, `submissions_total`, error rates). Don't trust intuition.
3. **Hypothesize narrowly.** What changed in the last 24 hours? Recent deploys, schema migrations, feature flags, third-party status pages.
4. **Verify the hypothesis** before acting. A bad hypothesis costs a deploy cycle.
5. **Document as you go.** The retro depends on the timeline.

## Common runbooks to consult

- [sandbox-stuck.md](./sandbox-stuck.md) — sandbox stuck or unresponsive.
- [db-restore.md](./db-restore.md) — DB corruption or rollback.
- [rotate-secrets.md](./rotate-secrets.md) — credential rotation, including leaks.

## Comms template

Pinned doc at incident start, updated on the cadence in the severity table.

```
# Incident YYYY-MM-DD — <short title>

- Status: Investigating | Identified | Monitoring | Resolved
- Severity: SEV<n>
- IC: <name>
- Started: <UTC>
- Resolved: <UTC or n/a>
- Customer-facing: yes/no
- Status page: <link>

## Summary
<one paragraph; updated as understanding improves>

## Impact
- Users affected: <count or %>
- Features affected: <list>
- Data integrity: <unaffected | suspected | confirmed loss>

## Timeline (UTC)
- 14:02 — Alert fires
- 14:05 — IC paged
- 14:07 — War-room open
- ...

## Actions taken
- ...

## Next update
By <UTC>.
```

## Public status page updates

Use only three states:

- **Investigating** — we know something is wrong, looking into it.
- **Identified** — we know the cause and are working on it.
- **Resolved** — fixed; monitoring for recurrence.

Be honest. Avoid jargon. No internal hostnames in public posts.

## Retro

Every SEV1 and SEV2 gets a retro within 5 business days. SEV3s get one if they recurred or surprised us.

### Retro template

```
# Retro: <incident title> — YYYY-MM-DD

- Severity: SEV<n>
- Duration: <minutes>
- IC: <name>
- Customer-facing: yes/no

## What happened
<short narrative — what users saw, what we saw>

## Timeline
<copy from the incident status doc>

## Root cause
<the actual cause, not "human error">

## What went well
- ...

## What went poorly
- ...

## Action items
| # | Action | Owner | Due | Ticket |
|---|---|---|---|---|

## Lessons
<short list — what we'd tell a new on-call about this class of incident>
```

### Retro rules

- **Blameless.** We critique systems and processes, not people.
- **Action items are real.** Each gets an owner and a date. We close the loop in the next retro.
- **Lessons get propagated.** If the retro surfaces a new failure mode, update the relevant runbook in the same PR as the retro doc.

## On-call expectations

- Acknowledge pages within 5 minutes (SEV1) / 15 minutes (SEV2) during your shift.
- Carry a working laptop and connectivity for the whole shift.
- Hand off cleanly at shift change: open incidents, ongoing watches, anything weird.
- Take retro time the next day if you worked an incident past midnight.

## References

- [IMPLEMENTATION_PLAN.md §21](../../IMPLEMENTATION_PLAN.md) (security & abuse)
- [IMPLEMENTATION_PLAN.md §22](../../IMPLEMENTATION_PLAN.md) (deployment)
- [docs/security.md](../security.md)
