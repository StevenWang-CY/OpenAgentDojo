# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# OpenAgentDojo sandbox worker — RQ worker that provisions and drives sandboxes.
#
# Built on the same FastAPI image so it shares the application code and
# SQLAlchemy models, but its CMD runs `rq worker` instead of uvicorn.
#
# Mounting `/var/run/docker.sock` into this container (see compose) is what
# lets it `docker run` per-session sandbox containers from the host daemon.
# Treat the socket as ROOT-equivalent: only the RQ worker should have it.
#
# Build context MUST be the repository root.
#   docker build -f infra/docker/sandbox-worker.Dockerfile -t agentarena/sandbox-worker:dev .
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12

# ---------- builder (mirrors api.Dockerfile) ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "uv>=0.4.0"

WORKDIR /app

COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/uv.lock* ./

RUN uv venv /opt/venv \
 && . /opt/venv/bin/activate \
 && if [ -f uv.lock ]; then \
        uv sync --frozen --no-dev; \
    else \
        uv pip install --no-cache .; \
    fi

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# The worker needs the docker CLI to drive the host daemon over the mounted
# socket. The docker.io client package brings in the CLI without a daemon.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        docker.io \
        git \
        libpq5 \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system arena \
 && useradd --system --gid arena --home /home/arena --create-home arena

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=arena:arena apps/api/pyproject.toml /app/pyproject.toml
COPY --chown=arena:arena apps/api/app /app/app
COPY --chown=arena:arena apps/api/alembic.ini /app/alembic.ini
COPY --chown=arena:arena apps/api/alembic /app/alembic
COPY --chown=arena:arena apps/api/scripts /app/scripts
# Sandbox seccomp profile lives next to the worker so it can be pinned at
# /app/infra/docker/seccomp.json regardless of the host bind-mount layout.
COPY --chown=arena:arena infra/docker/seccomp.json /app/infra/docker/seccomp.json

# Worker shares its content tree with the API container.
ENV MISSIONS_ROOT=/missions \
    ARENA_SKIP_MIGRATE=1 \
    ARENA_SKIP_SEED=1

EXPOSE 9181

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import redis,os,sys; r=redis.from_url(os.environ['REDIS_URL']); r.ping()" || exit 1

# Rootless runtime: drop privileges. In *dev compose* the bind-mounted
# /var/run/docker.sock is owned by root:docker on the host, so dev overrides
# can either set `user: "0:0"` or grant the arena uid membership in the docker
# group. In *prod* we run rootless Docker (or a host socket proxy) so the
# arena user can drive the daemon with no elevation — see
# docs/runbooks/sandbox-rootless.md for the production path.
USER arena

# tini handles signals. The worker shares the API image's entrypoint so any
# future shared init (e.g. waiting on Postgres) stays consistent across both
# services, but ARENA_SKIP_* flags above keep migrations/seed off the worker.
ENTRYPOINT ["/usr/bin/tini", "--"]
# Queue name MUST match Queue("provision", ...) in apps/api/app/workers/queue.py.
# Override at runtime to drain additional queues if/when we split work later.
CMD ["sh", "-c", "rq worker --url ${REDIS_URL} provision"]
