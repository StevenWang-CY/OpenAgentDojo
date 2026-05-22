# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# OpenAgentDojo API — production image for apps/api (FastAPI + uvicorn).
#
# Build context MUST be the repository root so we can copy apps/api/*.
#   docker build -f infra/docker/api.Dockerfile -t agentarena/api:dev .
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12

# ---------- builder ----------
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

# Copy only the lock + manifest first so dep resolution caches.
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/uv.lock* ./

# Resolve and install production deps into an isolated venv.
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
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libpq5 \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system arena \
 && useradd --system --gid arena --home /home/arena --create-home arena

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy the application source; alembic + scripts ride along so the same image
# can run migrations and CLIs without a rebuild.
COPY --chown=arena:arena apps/api/pyproject.toml /app/pyproject.toml
COPY --chown=arena:arena apps/api/app /app/app
COPY --chown=arena:arena apps/api/alembic.ini /app/alembic.ini
COPY --chown=arena:arena apps/api/alembic /app/alembic
COPY --chown=arena:arena apps/api/scripts /app/scripts
RUN chmod +x /app/scripts/entrypoint.sh

# Default mission content path inside the container — compose mounts the repo
# missions directory here read-only. Honoured by app.config.Settings.
ENV MISSIONS_ROOT=/missions

USER arena

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

# tini handles signals; entrypoint.sh runs migrations + mission loader before
# exec'ing the CMD. Override ARENA_SKIP_MIGRATE / ARENA_SKIP_SEED at runtime to
# bypass either step (used by the worker image and pytest).
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
