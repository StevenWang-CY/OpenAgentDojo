# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# agentarena/node20:1 — base sandbox image for Node 20 repo packs.
#
# Used as the `BASE_IMAGE` build-arg for `infra/docker/repo-pack.Dockerfile`
# when the mission's `repo.language_runtime` is `node20` (see mission.yaml).
#
# Build:
#   docker build -f infra/docker/node20.Dockerfile -t agentarena/node20:1 .
# ---------------------------------------------------------------------------

FROM node:26.3-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PNPM_HOME=/home/arena/.local/share/pnpm \
    PATH=/home/arena/.local/share/pnpm:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Toolchain expected by missions + the sandbox driver.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        git \
        jq \
        ripgrep \
 && rm -rf /var/lib/apt/lists/*

# Pin pnpm via corepack so every repo pack resolves the same lockfile semantics.
RUN corepack enable \
 && corepack prepare pnpm@9.12.0 --activate

# P1-3 — LSP for TypeScript repo packs. typescript-language-server is the
# canonical server used by VS Code's TS extension and Neovim; pinning the
# global install at build time keeps cold-start budget under 3 s. The
# typescript package is a peer dep used to resolve workspace `tsc` correctly.
RUN npm install -g --no-audit --no-fund \
        typescript@5.5.4 \
        typescript-language-server@4.3.3 \
 && npm cache clean --force

# Non-root user (uid 1000) for sandbox containers — never run repo code as root.
RUN groupadd --gid 1000 arena \
 && useradd --uid 1000 --gid arena --home-dir /home/arena --create-home --shell /bin/bash arena \
 && mkdir -p /workspace \
 && chown -R arena:arena /workspace /home/arena

# Shared docker runners — the grader expects ``/opt/runners`` to exist on
# every sandbox image so language-specific bridges (today: Go; future: TS
# slow-test bridges) live at a stable path. Bundling here keeps the layout
# consistent across the three base images even when no TS-specific runner
# exists yet.
COPY missions/_shared/docker/runners/ /opt/runners/

WORKDIR /workspace

# Optional: bake a repo pack into this image at build time.
# Defaults to empty so the base image stays generic; `repo-pack.Dockerfile`
# overrides this when building per-mission images.
ARG REPO_PACK_PATH=""
COPY --chown=arena:arena ${REPO_PACK_PATH:-.dockerignore} /workspace/

USER arena

# Pre-warm pnpm store if a lockfile is present so cold sandbox boots stay fast.
# `|| true` keeps the layer green for the bare base image (no lockfile yet).
RUN if [ -f /workspace/pnpm-lock.yaml ]; then \
        pnpm install --frozen-lockfile --prefer-offline; \
    else \
        true; \
    fi

CMD ["bash"]
