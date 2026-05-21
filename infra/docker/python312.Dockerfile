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

FROM python:3.12-slim-bookworm

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

# Non-root user (uid 1000) for sandbox containers.
RUN groupadd --gid 1000 arena \
 && useradd --uid 1000 --gid arena --home-dir /home/arena --create-home --shell /bin/bash arena \
 && mkdir -p /workspace \
 && chown -R arena:arena /workspace /home/arena

WORKDIR /workspace
USER arena

CMD ["bash"]
