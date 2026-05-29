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

## P1-3 — LSP binaries baked into the sandbox bases

Each runtime base image ships the language server its repo packs need so
the in-sandbox LSP spawned by `driver.spawn_lsp` (see
`apps/api/app/sandbox/lsp.py`) launches without a cold install:

| Runtime      | Image                 | LSP installed                            |
|--------------|-----------------------|------------------------------------------|
| `node20`     | `agentarena/node20:1` | `typescript-language-server` (+ `typescript`) |
| `python312`  | `agentarena/python312:1` | `pyright` (primary), `python-lsp-server` (fallback) |
| `go` (P1-1) | `agentarena/go122:1` | `gopls` (pinned `@v0.16.2`) |

The Go base image (`infra/docker/go122.Dockerfile`, shipped in P1-1) installs
its LSP with:

```dockerfile
RUN go install golang.org/x/tools/gopls@v0.16.2 \
 && cp /root/go/bin/gopls /usr/local/bin/gopls
```
