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

The packs themselves are registered as pnpm workspaces from the top-level
[`pnpm-workspace.yaml`](../../../pnpm-workspace.yaml). That means:

- `pnpm install` from the monorepo root installs deps for every pack.
- `pnpm --filter @demo/backend test:unit` runs a single pack's tests
  without going through Docker — useful while authoring a mission.

Do **not** add a separate `pnpm-workspace.yaml` inside a repo pack. The
monorepo's workspace file is the only one.

## Current packs

- **`fullstack-auth-demo/`** — Express + Vite/React + Vitest. Used by
  Mission 01 (and planned Missions 02, 03, 05, 06, 09, 10).
- _planned:_ **`data-api-demo/`** — FastAPI + SQLAlchemy + Pytest. Used by
  Missions 04, 07, 08.
