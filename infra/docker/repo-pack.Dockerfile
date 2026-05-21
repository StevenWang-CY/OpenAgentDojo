# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Repo-pack image template — bakes a mission's frozen repo into a tagged
# image derived from one of the language base images.
#
# Driven by `infra/scripts/build_repo_pack.sh`. Do not invoke directly unless
# you know which base image + repo path you want.
#
# Build-args:
#   BASE_IMAGE      e.g. agentarena/node20:1 or agentarena/python312:1
#   REPO_PATH       path (relative to build context) to the repo source
#   INITIAL_COMMIT  short SHA recorded as the pack version
#   PACK_NAME       human-readable pack id (for labels only)
#
# Example:
#   docker build \
#     -f infra/docker/repo-pack.Dockerfile \
#     --build-arg BASE_IMAGE=agentarena/node20:1 \
#     --build-arg REPO_PATH=missions/_shared/repos/fullstack-auth-demo \
#     --build-arg INITIAL_COMMIT=abc123de \
#     --build-arg PACK_NAME=fullstack-auth-demo \
#     -t agentarena/fullstack-auth-demo:abc123de .
# ---------------------------------------------------------------------------

ARG BASE_IMAGE=agentarena/node20:1
FROM ${BASE_IMAGE}

ARG REPO_PATH
ARG INITIAL_COMMIT=unknown
ARG PACK_NAME=unknown

LABEL org.agentarena.pack="${PACK_NAME}" \
      org.agentarena.commit="${INITIAL_COMMIT}" \
      org.agentarena.role="repo-pack"

USER root

# Wipe whatever the base image staged (`/workspace` may be empty or a stub
# from the bare base build) and copy the frozen repo source in fresh.
RUN rm -rf /workspace && mkdir -p /workspace && chown -R arena:arena /workspace

COPY --chown=arena:arena ${REPO_PATH}/ /workspace/

USER arena
WORKDIR /workspace

# Initialise a git history so `git diff <initial_commit>..HEAD` works for the
# grading layer. If the source already has a `.git` directory we leave it
# alone (preserves the real history when missions vendor a sub-repo).
RUN if [ ! -d .git ]; then \
        git init -q \
        && git config user.email "agent@agentarena.dev" \
        && git config user.name "AgentArena Pack Builder" \
        && git add -A \
        && git commit -q -m "initial: ${PACK_NAME}@${INITIAL_COMMIT}" \
        && git tag "pack-${INITIAL_COMMIT}"; \
    fi

# Setup commands declared in mission.yaml normally run inside the live
# sandbox, but pre-running them here keeps cold starts fast. Tolerate
# absence of a lockfile so this layer stays useful for partial packs.
RUN if [ -f pnpm-lock.yaml ]; then \
        pnpm install --frozen-lockfile --prefer-offline; \
    elif [ -f uv.lock ] || [ -f pyproject.toml ]; then \
        uv sync --frozen 2>/dev/null || uv pip install -e . 2>/dev/null || true; \
    fi

CMD ["bash"]
