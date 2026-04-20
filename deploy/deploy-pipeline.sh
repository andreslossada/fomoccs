#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Use exported env vars from deploy.sh, or set defaults for standalone use
: "${PROJECT_ID:=momaverse}"
: "${DOCKER_REPO:=us-central1-docker.pkg.dev/momaverse/momaverse-docker}"
: "${PIPELINE_JOB:=momaverse-pipeline}"
: "${REGION:=us-central1}"
: "${REDIS_URL:?REDIS_URL must be set — pipeline publishes Celery tasks to this broker}"

PIPELINE_DIR="$PROJECT_ROOT/pipeline"

if [[ ! -f "$PIPELINE_DIR/Dockerfile" ]]; then
  echo "Error: pipeline Dockerfile not found at $PIPELINE_DIR/Dockerfile" >&2
  exit 1
fi

# === Capture Current Image for Rollback ===
PREV_IMAGE=$(gcloud run jobs describe "${PIPELINE_JOB}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(template.template.containers[0].image)' 2>/dev/null || echo "unknown")
echo "  Current image: ${PREV_IMAGE}"

GIT_SHA="$(git rev-parse --short HEAD)"
IMAGE="${DOCKER_REPO}/pipeline:${GIT_SHA}"

echo "=== Configuring Docker Auth ==="
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet --project="${PROJECT_ID}"

echo ""
echo "=== Building Pipeline Image ==="
echo "  Image: ${IMAGE}"
echo "  (This may take a few minutes due to Playwright/Chromium)"
docker build --platform linux/amd64 \
  -t "${IMAGE}" \
  "$PIPELINE_DIR"

echo ""
echo "=== Pushing Pipeline Image ==="
docker push "${IMAGE}"

echo ""
echo "=== Updating Cloud Run Job ==="
gcloud run jobs update "${PIPELINE_JOB}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --update-env-vars="REDIS_URL=${REDIS_URL}"

echo ""
echo "=== Pipeline deployment complete ==="
echo "  REDIS_URL: ${REDIS_URL%%:*}://**** (redacted)"
echo "  Rollback: gcloud run jobs update ${PIPELINE_JOB} --image=${PREV_IMAGE} --region=${REGION} --project=${PROJECT_ID}"
