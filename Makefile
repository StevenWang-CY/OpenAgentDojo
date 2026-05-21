# ---------------------------------------------------------------------------
# AgentSupervisor Arena — convenience Make targets.
#
# These wrap the pnpm scripts and shell scripts the rest of the infra exposes,
# so contributors can use whichever interface they prefer.
# ---------------------------------------------------------------------------

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

.DEFAULT_GOAL := help

REPO_ROOT := $(shell pwd)
COMPOSE_FILE := infra/compose/docker-compose.yml

.PHONY: help
help:
	@echo "AgentArena make targets:"
	@echo "  make up            — start the compose stack (postgres+redis+minio+api+worker+web)"
	@echo "  make down          — stop the compose stack"
	@echo "  make logs          — tail compose logs"
	@echo "  make seed          — run migrations + load mission catalog (idempotent)"
	@echo "  make dev           — up + seed + print URLs (one-shot bootstrap)"
	@echo "  make test          — pnpm test (web) + uv pytest (api)"
	@echo "  make lint          — pnpm lint (web) + ruff (api)"
	@echo "  make sandbox-image — build every repo-pack image from missions/_shared/repos/*"
	@echo "  make determinism   — replay compute_score 5x against a fixture (no DB needed)"
	@echo "  make ci-local      — run the full CI pipeline locally"

.PHONY: up
up:
	pnpm compose:up

.PHONY: down
down:
	pnpm compose:down

.PHONY: logs
logs:
	pnpm compose:logs

.PHONY: seed
seed:
	# Runs alembic upgrade head + python -m app.missions.loader (implicit
	# --seed via the loader's argparse default). Matches the
	# `python -m app.missions.loader --seed` step in apps/api/scripts/entrypoint.sh —
	# the --seed flag is the default in the loader's argparse, so both paths
	# upsert the catalog. Keep this comment in sync with entrypoint.sh.
	infra/scripts/seed_dev.sh

.PHONY: dev
dev:
	infra/scripts/dev.sh

.PHONY: test
test:
	pnpm -r test
	cd apps/api && uv run pytest

.PHONY: test-missions
test-missions:
	cd apps/api && uv run python3 -m pytest tests/missions -v

.PHONY: determinism
determinism:
	# Replay compute_score 5x against a fixture mission; fails if any drift.
	# Mirrors .github/workflows/determinism.yml so the nightly job and the
	# local check stay in sync. Override with: make determinism RUNS=10
	cd apps/api && uv run python scripts/replay_determinism.py --runs $${RUNS:-5}

.PHONY: lint
lint:
	pnpm -r lint
	cd apps/api && uv run ruff check app && uv run ruff format --check app

.PHONY: sandbox-image
sandbox-image:
	infra/scripts/build_all_repo_packs.sh

.PHONY: typecheck
typecheck:
	pnpm -r typecheck
	cd apps/api && uv run mypy app

# Mirror of .github/workflows/ci.yml — useful before pushing.
.PHONY: ci-local
ci-local: lint typecheck test
	cd apps/api && uv run python scripts/validate_missions.py
	@echo "ci-local: all checks passed"
