# ADR 0005: Sandbox Isolation — Rootless Docker, No-Network Default

- Status: Accepted
- Date: 2026-05-21
- Deciders: AgentSupervisor Arena team

## Context

Each session gives a user a real shell inside a real repository running real test commands. The platform must:

1. Prevent code in one user's sandbox from reading another user's data or escaping to the host.
2. Stop network exfiltration of sandbox contents (or worse, of platform secrets).
3. Cap CPU / memory / disk so a runaway test can't take down the worker.
4. Provide a path that works on a contributor's laptop without Docker installed (otherwise local dev breaks).

## Decision

**Production: Docker, rootless, one container per session.**

- Base images per language runtime (`agentarena/node20:1`, `agentarena/python312:1`) baked with toolchains; user repos mount as overlayfs on top.
- `--cap-drop=ALL` — no Linux capabilities.
- `--network=none` by default. Scenarios needing package installs use a pre-baked image, not a live network.
- cgroups v2 caps: 1 vCPU, 2 GB RAM, 1 GB disk, 30-minute hard lifetime.
- Seccomp profile drops syscalls outside the standard application set.
- No host mounts. The sandbox sees `/workspace` (its overlay) and `/grader` (read-only, mounted only at submit time).

**Local dev: subprocess fallback driver.**

- Gated behind `SANDBOX_DRIVER=local`.
- Runs commands in a temp directory using `subprocess`. **No isolation guarantees.**
- A loud warning banner appears in the UI. Never enabled in prod (config validation rejects it when `ENV=production`).

## Consequences

### Positive

- Sandbox breakouts require chaining a kernel CVE *and* a misconfigured cap *and* a writable mount — three things we control.
- No network = no exfiltration of `keys.md` or sandbox contents.
- The local driver keeps contributor onboarding fast (15-minute path from clone to running).
- The same `Sandbox` interface backs both drivers; tests run identically on CI and laptops.

### Negative

- Container cold-start is 1–3 seconds; we pre-warm a small pool per popular mission to keep p95 provision time under 25 s.
- Adding a new language runtime means baking a new base image and pinning its deps.
- The local driver is a foot-gun — we mitigate with config guards, the UI banner, and CI checks for `SANDBOX_DRIVER` in prod manifests.

### Neutral

- Docker-in-Docker on Fly Machines needs a privileged worker pool; we accept that complexity in the deploy layer (see [IMPLEMENTATION_PLAN.md §22](../../IMPLEMENTATION_PLAN.md)).

## Alternatives considered

- **Firecracker microVMs.** Stronger isolation, lower cold-start than full VMs, but operationally heavier. We revisit if we ever expose sandboxes to fully untrusted code (e.g. user-authored missions).
- **gVisor.** Good defense-in-depth, but adds a syscall-translation overhead that hurts test-run latency. Considered for v2.
- **Browser-side WASM execution (no server sandbox).** Rejected: no real shell, no real test runner, no real failure modes.
- **Process-level isolation (subprocess only) in prod.** Rejected for safety. The local driver exists strictly as a dev fallback.

## References

- [IMPLEMENTATION_PLAN.md §9](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §21](../../IMPLEMENTATION_PLAN.md)
- [docs/security.md](../security.md)
- [docs/runbooks/sandbox-stuck.md](../runbooks/sandbox-stuck.md)
