#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Use exported env vars from deploy.sh, or set defaults for standalone use
: "${PROJECT_ID:=momaverse}"
: "${DOCKER_REPO:=us-central1-docker.pkg.dev/momaverse/momaverse-docker}"
: "${BACKEND_SERVICE:=momaverse-backend}"
: "${BACKEND_WORKER_SERVICE:=momaverse-backend-worker}"
: "${REGION:=us-central1}"
: "${REDIS_URL:?REDIS_URL must be set — backend worker consumes Celery tasks from this broker}"

BACKEND_DIR="$PROJECT_ROOT/backend"

if [[ ! -f "$BACKEND_DIR/Dockerfile" ]]; then
  echo "Error: backend Dockerfile not found at $BACKEND_DIR/Dockerfile" >&2
  exit 1
fi

# === Capture Current Revision for Rollback ===
PREV_REVISION=$(gcloud run revisions list \
  --service="${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(REVISION)' \
  --limit=1 2>/dev/null || echo "unknown")
echo "  Current revision: ${PREV_REVISION}"

GIT_SHA="$(git rev-parse --short HEAD)"
IMAGE="${DOCKER_REPO}/backend:${GIT_SHA}"

echo "=== Configuring Docker Auth ==="
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet --project="${PROJECT_ID}"

echo ""
echo "=== Building Backend Image ==="
echo "  Image: ${IMAGE}"
docker build --platform linux/amd64 \
  -t "${IMAGE}" \
  "$BACKEND_DIR"

echo ""
echo "=== Pushing Backend Image ==="
docker push "${IMAGE}"

echo ""
echo "=== Updating Cloud Run Service ==="
gcloud run deploy "${BACKEND_SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}"

echo ""
SERVICE_URL=$(gcloud run services describe "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')
echo "=== Backend deployed: ${SERVICE_URL} ==="
echo "  Rollback: gcloud run services update-traffic ${BACKEND_SERVICE} --to-revisions=${PREV_REVISION}=100 --region=${REGION} --project=${PROJECT_ID}"

echo ""
echo "=== Deploying Backend Celery Worker ==="
gcloud run deploy "${BACKEND_WORKER_SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --command="celery" \
  --args="-A,api.celery_app,worker,--loglevel=info,--concurrency=4" \
  --set-env-vars="REDIS_URL=${REDIS_URL}" \
  --no-cpu-throttling \
  --min-instances=1

echo ""
WORKER_URL=$(gcloud run services describe "${BACKEND_WORKER_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')
echo "=== Worker deployed: ${WORKER_URL} ==="
echo "  Rollback: gcloud run deploy ${BACKEND_WORKER_SERVICE} --image=${IMAGE} --region=${REGION} --project=${PROJECT_ID} (re-deploy previous SHA)"
