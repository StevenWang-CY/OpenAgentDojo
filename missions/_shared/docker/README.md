# missions/_shared/docker/

This directory is intentionally a thin shim. The actual sandbox base
images (`agentarena/node20:1`, `agentarena/python312:1`) live under
[`infra/docker/`](../../../infra/docker/) and are owned by the infra
build pipeline (see `IMPLEMENTATION_PLAN.md` §9.1 and §M2).

If you came here looking for the Node 20 Dockerfile, open
[`infra/docker/node20.Dockerfile`](../../../infra/docker/node20.Dockerfile)
instead. This folder is reserved for mission-specific Docker overrides
(e.g. additional system packages) that may be needed by future scenarios
beyond the standard base image. None exist today.
