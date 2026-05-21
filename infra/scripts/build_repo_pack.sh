#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build_repo_pack.sh — bake a frozen mission repo pack into a Docker image.
#
# Reads the source at missions/_shared/repos/<pack_name>/ and produces:
#   agentarena/<pack_name>:<commit_sha>
#
# Usage:
#   infra/scripts/build_repo_pack.sh <pack_name> <commit_sha> [base_image]
#
# Args:
#   pack_name    Folder name under missions/_shared/repos/
#   commit_sha   Short SHA recorded in the image tag + label
#   base_image   Defaults to agentarena/node20:1; pass agentarena/python312:1
#                for Python-runtime missions.
#
# Examples:
#   infra/scripts/build_repo_pack.sh fullstack-auth-demo abc123de
#   infra/scripts/build_repo_pack.sh data-api-demo def456ab agentarena/python312:1
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <pack_name> <commit_sha> [base_image]" >&2
  exit 64
fi

PACK_NAME="$1"
COMMIT_SHA="$2"
BASE_IMAGE="${3:-agentarena/node20:1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_PATH="missions/_shared/repos/${PACK_NAME}"
ABS_REPO_PATH="${REPO_ROOT}/${REPO_PATH}"

if [[ ! -d "${ABS_REPO_PATH}" ]]; then
  echo "[build_repo_pack] no such pack: ${ABS_REPO_PATH}" >&2
  exit 1
fi

IMAGE_TAG="agentarena/${PACK_NAME}:${COMMIT_SHA}"

echo "[build_repo_pack] pack       : ${PACK_NAME}"
echo "[build_repo_pack] commit_sha : ${COMMIT_SHA}"
echo "[build_repo_pack] base_image : ${BASE_IMAGE}"
echo "[build_repo_pack] repo_path  : ${REPO_PATH}"
echo "[build_repo_pack] image_tag  : ${IMAGE_TAG}"

cd "${REPO_ROOT}"

docker build \
  --progress=plain \
  -f infra/docker/repo-pack.Dockerfile \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "REPO_PATH=${REPO_PATH}" \
  --build-arg "INITIAL_COMMIT=${COMMIT_SHA}" \
  --build-arg "PACK_NAME=${PACK_NAME}" \
  -t "${IMAGE_TAG}" \
  -t "agentarena/${PACK_NAME}:latest" \
  .

echo "[build_repo_pack] built ${IMAGE_TAG}"
