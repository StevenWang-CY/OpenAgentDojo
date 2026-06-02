# missions/_shared/repos/

Frozen base repositories ("Repo Packs") used by missions. Each pack is a
self-contained, runnable project pinned to an exact set of dependencies
so that hidden-test runs are reproducible.

## Why "frozen"?

Sandbox grading must be deterministic. To get there, every mission
pinned to a repo pack uses:

1. An exact `initial_commit` SHA recorded in the mission manifest.
2. An exact `pnpm-lock.yaml` (or `uv.lock`) at that commit.
3. A pinned base image (`agentarena/<runtime>:<tag>`) with the package
   manager pre-warmed (M2+).

Changing any of these requires a manifest version bump and a re-bake of
the corresponding image (`infra/scripts/build_repo_pack.sh`).

## Workspace integration

The packs are intentionally **not** registered in the top-level
[`pnpm-workspace.yaml`](../../../pnpm-workspace.yaml) (which scopes only
`apps/web` and `packages/*`). Each Node-based pack installs standalone
from its own `pnpm-workspace.yaml` + `pnpm-lock.yaml`, so the sandbox
provisioner can copy a pack without dragging in symlinks that point at
this monorepo's `.pnpm` store. That means:

- `pnpm install` from the monorepo root does **not** touch the packs;
  run `pnpm install` inside the pack directory instead.
- `pnpm --filter @demo/backend test:unit` from inside a pack runs that
  pack's tests without going through Docker — useful while authoring a
  mission.

Keep each pack's own `pnpm-workspace.yaml`; do **not** add the packs to
the monorepo's workspace file.

## Current packs

- **`fullstack-auth-demo/`** — Express + Vite/React + Vitest. Used by
  Missions 00, 01, 02, 03, 05, 06, 09, 10.
- **`data-api-demo/`** — FastAPI + SQLAlchemy + Pytest. Used by
  Missions 04, 07, 08.
- **`go-orders-service/`** — Go + `go test`. Used by Missions 11, 12, 13.
