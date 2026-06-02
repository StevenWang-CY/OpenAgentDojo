# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# agentarena/python312:1 — base sandbox image for Python 3.12 repo packs.
#
# Used as the `BASE_IMAGE` build-arg for `infra/docker/repo-pack.Dockerfile`
# when the mission's `repo.language_runtime` is `python312`.
#
# Build:
#   docker build -f infra/docker/python312.Dockerfile -t agentarena/python312:1 .
# ---------------------------------------------------------------------------

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/home/arena/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Toolchain expected by missions + the sandbox driver.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        coreutils \
        curl \
        git \
        jq \
        ripgrep \
 && rm -rf /var/lib/apt/lists/*

# uv — fast resolver/installer; matches what the host repo uses.
RUN pip install --no-cache-dir "uv>=0.4.0"

# P1-3 — LSP for Python repo packs. pyright is preferred over pylsp because
# its diagnostics match what VS Code's "Python" extension shows; pylsp is
# kept as the documented fallback for restricted deployments. Both ship
# stdio servers (``pyright-langserver --stdio`` / ``pylsp``) that the
# sandbox driver auto-discovers via PATH.
#
# pyright needs node at runtime — we bring in the Debian package because
# this image is otherwise pure Python (no corepack/pnpm setup).
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && npm install -g --no-audit --no-fund pyright@1.1.378 \
 && npm cache clean --force \
 && pip install --no-cache-dir "python-lsp-server==1.11.0" \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (uid 1000) for sandbox containers.
RUN groupadd --gid 1000 arena \
 && useradd --uid 1000 --gid arena --home-dir /home/arena --create-home --shell /bin/bash arena \
 && mkdir -p /workspace \
 && chown -R arena:arena /workspace /home/arena

# Shared docker runners — the grader expects ``/opt/runners`` to exist on
# every sandbox image so language-specific bridges (today: Go; future: Py
# slow-test bridges) live at a stable path. Bundling here keeps the layout
# consistent across the three base images even when no Python-specific
# runner exists yet.
COPY missions/_shared/docker/runners/ /opt/runners/

WORKDIR /workspace
USER arena

CMD ["bash"]
