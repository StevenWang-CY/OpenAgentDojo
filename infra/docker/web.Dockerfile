# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# OpenAgentDojo web — production image for apps/web (Next.js 15 standalone).
#
# Build context MUST be the repository root (uses the pnpm workspace).
#   docker build -f infra/docker/web.Dockerfile -t agentarena/web:dev .
# ---------------------------------------------------------------------------

ARG NODE_VERSION=20.18

# ---------- deps ----------
FROM node:${NODE_VERSION}-alpine AS deps

RUN apk add --no-cache libc6-compat \
 && corepack enable \
 && corepack prepare pnpm@9.12.0 --activate

WORKDIR /repo

# Copy only the manifests for the cache layer. We copy ONLY the per-package
# package.json files (not the whole packages/ + missions/ trees) so the deps
# stage is invalidated only when a manifest changes, not on every source edit.
# Every workspace member declared in pnpm-workspace.yaml must have its
# package.json present here or `pnpm install --frozen-lockfile` will fail
# because pnpm-lock.yaml has an `importers:` entry for each.
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/shared-types/package.json ./packages/shared-types/package.json
COPY missions/_shared/repos/fullstack-auth-demo/package.json ./missions/_shared/repos/fullstack-auth-demo/package.json
COPY missions/_shared/repos/fullstack-auth-demo/backend/package.json ./missions/_shared/repos/fullstack-auth-demo/backend/package.json
COPY missions/_shared/repos/fullstack-auth-demo/frontend/package.json ./missions/_shared/repos/fullstack-auth-demo/frontend/package.json
COPY missions/_shared/repos/data-api-demo/package.json ./missions/_shared/repos/data-api-demo/package.json
COPY missions/_shared/repos/data-api-demo/frontend/package.json ./missions/_shared/repos/data-api-demo/frontend/package.json

# Frozen lockfile in CI: drift between pnpm-lock.yaml and package.json must
# fail the build loudly rather than silently regenerating the lockfile.
RUN pnpm install --frozen-lockfile --filter @arena/web... --prod=false

# ---------- builder ----------
FROM node:${NODE_VERSION}-alpine AS builder

RUN apk add --no-cache libc6-compat \
 && corepack enable \
 && corepack prepare pnpm@9.12.0 --activate

WORKDIR /repo

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY package.json pnpm-workspace.yaml ./
COPY packages ./packages
COPY apps/web ./apps/web

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN pnpm --filter @arena/web build

# ---------- runtime ----------
FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apk add --no-cache curl tini \
 && addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

WORKDIR /app

# Next.js standalone output already bundles only what's needed.
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/public ./apps/web/public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:3000/ >/dev/null || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "apps/web/server.js"]
