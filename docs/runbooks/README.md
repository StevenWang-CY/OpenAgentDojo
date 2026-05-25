# Runbooks

Operational playbooks for common incidents and routine ops. Keep them short, current, and testable.

## Index

| Runbook | Use when |
|---|---|
| [deploy.md](./deploy.md) | Shipping a release — pre-flight, migration ordering, and the dual-heads fix |
| [rotate-secrets.md](./rotate-secrets.md) | Quarterly rotation, or any time a secret may have leaked |
| [sandbox-stuck.md](./sandbox-stuck.md) | A sandbox doesn't reach `active`, freezes, or outlives its cap |
| [db-restore.md](./db-restore.md) | DB corruption, point-in-time recovery, or refreshing staging |
| [incident-response.md](./incident-response.md) | Any user-visible incident or security event |
| [email-deliverability.md](./email-deliverability.md) | Magic-link emails are bouncing, throttling, or sender reputation drops |

## Conventions

- Each runbook covers exactly one operational surface.
- Steps are copy-pasteable. Placeholders are `<like this>`.
- "What this runbook does **not** cover" section is mandatory — keep scope explicit.
- Test runbooks at least once per quarter via game-day exercises. A runbook nobody has executed is just hopeful prose.
