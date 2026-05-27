#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build_all_repo_packs.sh — build every frozen repo pack into its own image.
#
# Iterates missions/_shared/repos/*/ and shells out to build_repo_pack.sh.
# Picks the base image per pack name:
#   data-api-demo      -> agentarena/python312:1
#   go-orders-service  -> agentarena/go122:1
#   anything else      -> agentarena/node20:1
#
# The commit SHA tag is derived from the working tree's HEAD (short SHA) so
# packs stay reproducible alongside the platform code. Override per-pack by
# placing a `.commit` file inside the pack directory with the desired tag.
#
# Usage:
#   infra/scripts/build_all_repo_packs.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PACKS_DIR="${REPO_ROOT}/missions/_shared/repos"

if [[ ! -d "${PACKS_DIR}" ]]; then
  echo "[build_all_repo_packs] no packs dir: ${PACKS_DIR}" >&2
  exit 1
fi

# Fallback commit SHA: short HEAD of the platform repo.
DEFAULT_SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo dev)"

shopt -s nullglob
found_any=0

for pack_dir in "${PACKS_DIR}"/*/; do
  pack_name="$(basename "${pack_dir}")"

  # Allow per-pack pin via missions/_shared/repos/<pack>/.commit
  if [[ -f "${pack_dir}.commit" ]]; then
    commit_sha="$(<"${pack_dir}.commit")"
    commit_sha="${commit_sha//[[:space:]]/}"
  else
    commit_sha="${DEFAULT_SHA}"
  fi

  case "${pack_name}" in
    data-api-demo)      base_image="agentarena/python312:1" ;;
    go-orders-service)  base_image="agentarena/go122:1" ;;
    *)                  base_image="agentarena/node20:1" ;;
  esac

  echo "============================================================"
  echo "[build_all_repo_packs] building ${pack_name}@${commit_sha}"
  echo "[build_all_repo_packs] base    : ${base_image}"
  echo "============================================================"

  "${SCRIPT_DIR}/build_repo_pack.sh" "${pack_name}" "${commit_sha}" "${base_image}"
  found_any=1
done

if (( found_any == 0 )); then
  echo "[build_all_repo_packs] no packs found under ${PACKS_DIR}" >&2
  exit 1
fi

echo "[build_all_repo_packs] all packs built"
