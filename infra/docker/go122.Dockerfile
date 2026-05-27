# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# agentarena/go122:1 — base sandbox image for Go 1.22 repo packs (P1-1).
#
# Used as the `BASE_IMAGE` build-arg for `infra/docker/repo-pack.Dockerfile`
# when the mission's `repo.language_runtime` is `go122` (see mission.yaml).
# The ``go-orders-service`` pack is the first consumer.
#
# Build:
#   docker build -f infra/docker/go122.Dockerfile -t agentarena/go122:1 .
# ---------------------------------------------------------------------------

FROM golang:1.22-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    CGO_ENABLED=0 \
    GOFLAGS=-mod=readonly \
    GOPATH=/home/arena/go \
    PATH=/home/arena/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Toolchain expected by missions + the sandbox driver. ``git`` and
# ``ca-certificates`` ship with golang:1.22-bookworm already but listing them
# here keeps the image reproducible if Debian ever changes the base set.
# ``python3`` is needed by ``missions/_shared/docker/runners/go-runner.sh``
# (the bridge that turns ``go test -json`` events into the grader envelope).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        git \
        jq \
        python3 \
        ripgrep \
 && rm -rf /var/lib/apt/lists/*

# P1-3 — LSP for Go repo packs. gopls is the canonical server used by VS Code
# and Neovim; pinning the version at build time keeps cold-start budget
# predictable and avoids the agent racing against a network-resolved install.
RUN go install golang.org/x/tools/gopls@v0.16.2 \
 && mv /root/go/bin/gopls /usr/local/bin/gopls \
 && rm -rf /root/go

# Non-root user (uid 1000) for sandbox containers — never run repo code as root.
RUN groupadd --gid 1000 arena \
 && useradd --uid 1000 --gid arena --home-dir /home/arena --create-home --shell /bin/bash arena \
 && mkdir -p /workspace /home/arena/go/bin \
 && chown -R arena:arena /workspace /home/arena

# Shared docker runners — the grader invokes ``/opt/runners/go-runner.sh``
# inside the sandbox to bridge ``go test -json`` events into the canonical
# ``{name,status,duration_ms,file}`` envelope shared with the TS/Py runners.
COPY missions/_shared/docker/runners/ /opt/runners/
RUN chmod +x /opt/runners/go-runner.sh

WORKDIR /workspace
USER arena

CMD ["bash"]
