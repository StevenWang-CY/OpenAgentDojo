# Contributing to OpenAgentDojo

Thanks for your interest in OpenAgentDojo. This project exists to teach
developers how to supervise AI coding agents — every code path is in
service of that goal. The fastest way to help is to **author a new mission**
(see §3 below); validators, UI work, and bug fixes are equally welcome but
the mission catalogue is what gives the platform its educational reach.

Before you start, skim [CONTEXT.md](CONTEXT.md) for the domain vocabulary
and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the architectural
shape. Both are short and load-bearing — every reviewer expects you to
have read them.

---

## 1. Welcome

OpenAgentDojo is open source under the [Apache License 2.0](LICENSE). You
are free to fork, study, and run the code; you are free to redistribute
derivatives under the same terms. By contributing you certify the
[Developer Certificate of Origin (DCO)](https://developercertificate.org/)
for each commit — practically that means signing your commits with
`git commit -s`. CI rejects unsigned commits.

We do not require a CLA. The DCO is sufficient for an Apache 2.0 project.

## 2. Setup in 30 seconds

```bash
pnpm install
cd apps/api && uv sync && cd ../..
pnpm compose:up                          # postgres + redis + minio + mailhog
cd apps/api && uv run alembic upgrade head
cd apps/api && uv run python -m app.missions.loader   # seed the catalog
cd apps/api && uv run uvicorn app.main:app --reload --port 8000   # one terminal
pnpm --filter @arena/web dev                                      # another terminal
```

The full version (with docker compose, sandbox driver options, common
gotchas) lives at [docs/onboarding.md](docs/onboarding.md).

## 3. The high-leverage contribution: a new mission

Missions are the product. Adding one is the single highest-value PR you
can ship.

1. **Propose first.** Open an issue using the
   [`scenario-proposal`](.github/ISSUE_TEMPLATE/scenario-proposal.md)
   template. State the failure mode, target difficulty, expected context
   shape, an outline of the agent patch, and a sketch of the hidden tests.
   Get design sign-off on the issue before you write the manifest — a
   wrong failure-mode framing is expensive to undo after the manifest
   ships.
2. **Copy the scenario template.** Start from
   [`docs/scenarios/template.md`](docs/scenarios/template.md), save as
   `docs/scenarios/<NN>-<id>.md`, and fill in the brief.
3. **Create the mission folder.** Under `missions/<NN>-<id>/`, ship the
   files declared in IMPLEMENTATION_PLAN.md §29.1's mission checklist:
   `mission.yaml`, `agent_patch.diff`, `ideal_solution.md`,
   `ideal_solution.diff`, `acceptance.yaml`, `hidden_tests/`,
   `expected_context.yaml` (or the equivalent fields in `mission.yaml`).
4. **Validate locally.**
   ```bash
   pnpm validate:missions
   cd apps/api && uv run pytest tests/missions -v
   ```
   Both must be green.
5. **Open the PR.** Link the scenario design note from §1 and confirm CI
   is green. PR title prefix: `feat(mission): <short title>`.

Missions are reviewed by a maintainer who runs the mission end-to-end in
their workspace before merging.

## 4. Bug fixes

- Every bug fix lands with a regression test. The test must fail without
  the fix and pass with it. If the bug is in a code path that's hard to
  unit-test, prefer integration tests over no tests.
- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `fix(scope): short description`. Common scopes: `grading`, `sessions`,
  `sandbox`, `web`, `auth`, `mission`.
- A bug-fix PR should change as little as possible. Refactors live in
  their own PR.

## 5. New validators

The grading engine's structural validators live at
[`apps/api/app/grading/validators/`](apps/api/app/grading/validators/).
Read one (`forbidden.py` is a good model) before writing yours.
Every validator MUST be deterministic — no clocks, no random seeds, no
network. The grading replay loop assumes the same inputs produce the
same `ValidatorResult`. If your validator needs to read the workspace,
use the `fs_reader` shim passed into the validator — never shell out.

## 6. UI changes

The dojo aesthetic (tight monospace, accent-blue chrome, deliberate
restraint) is load-bearing for the product story. Before you ship UI
work:

- Open a `design-proposal` issue describing what surface you're changing
  and what visual intent you're aiming for. Get a quick read from a
  maintainer; visual coherence is hard to add back after a PR has shipped.
- Prefer extending the existing token vocabulary
  (`apps/web/app/globals.css`, the Tailwind config) over inventing new
  colours, radii, or shadows.
- Run the accessibility suite: `pnpm --filter @arena/web test:axe`.

## 7. What we will not merge

- **LLM calls on the grading hot path.** The grader is deterministic
  per [ADR 0002](docs/adr/0002-deterministic-agent.md). Adding an LLM
  call to `app/grading/` is a fast path to a closed PR.
- **Mission content that needs real network access in the sandbox.**
  Sandboxes run with `--network=none`. If your mission needs an external
  service, mock it in the repo pack.
- **Frontend dependencies that add ≥50 KB gzipped to the bundle** without
  a justification that holds up under review. We measure on every PR.
- **PRs without tests.** New endpoints need pytest; new components need
  vitest; new flows need playwright.
- **Breaking changes to the supervision-event schema** without an ADR.
  The event log is append-only and consumed by the grader, the replay
  tool, and any downstream analyser. Renames are breaking changes.
- **Commits without `Signed-off-by:`.** CI's DCO check enforces this.

## 8. Commit + PR conventions

- **Conventional Commits.** Prefixes: `feat`, `fix`, `chore`, `docs`,
  `refactor`, `test`, `perf`. Scope optional but encouraged.
- **PRs reference an issue.** "Closes #123" in the description.
- **Squash-merge is the default.** Your PR's title becomes the squash
  commit message; keep it tight.
- **DCO sign-off required.** Configure git once:
  ```bash
  git config user.name  "Your Name"
  git config user.email "you@example.com"
  git config commit.gpgsign false   # only if you don't already sign
  ```
  Then commit with `-s`:
  ```bash
  git commit -s -m "fix(grading): off-by-one in diff minimality scoring"
  ```
  The `Signed-off-by: Your Name <you@example.com>` line in the commit
  message is what the DCO check looks for.

## 9. Reporting security issues

Please **do not open a public issue for security vulnerabilities.** See
[SECURITY.md](SECURITY.md) for the responsible-disclosure process. We
acknowledge within 2 business days and disclose publicly within 90 days
of a fix shipping.

---

Thanks again. The reviewer queue is small; a tight PR with the scenario
design note attached and a green CI run usually lands within a week.
