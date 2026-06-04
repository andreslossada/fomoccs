#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Use exported env vars from deploy.sh, or set defaults for standalone use
: "${PROJECT_ID:=fomoccs-caracas}"
: "${BACKEND_SERVICE:=fomoccs-backend}"
: "${REGION:=us-central1}"
: "${FRONTEND_BUCKET:=gs://fomoccs-frontend}"

cd "$PROJECT_ROOT"

if [[ ! -f "package.json" ]]; then
  echo "Error: package.json not found at $PROJECT_ROOT" >&2
  exit 1
fi

echo "=== Installing Dependencies ==="
npm ci --ignore-scripts

echo ""
echo "=== Fetching Backend URL ==="
BACKEND_URL=$(gcloud run services describe "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')
echo "  Backend URL: ${BACKEND_URL}"

echo ""
echo "=== Building Frontend ==="
API_BASE_URL="${BACKEND_URL}" npm run build

DIST_DIR="$PROJECT_ROOT/dist"
if [[ ! -d "$DIST_DIR" ]]; then
  echo "Error: build output directory not found at $DIST_DIR" >&2
  exit 1
fi

# Sanity check: verify build produced a minimum viable output
if [[ ! -f "$DIST_DIR/index.html" ]]; then
  echo "Error: dist/index.html not found — build may be broken, aborting sync" >&2
  exit 1
fi

FILE_COUNT=$(find "$DIST_DIR" -type f | wc -l | tr -d ' ')
if [[ "$FILE_COUNT" -lt 3 ]]; then
  echo "Error: dist/ contains only ${FILE_COUNT} files — build may be incomplete, aborting sync" >&2
  exit 1
fi

echo ""
echo "=== Syncing to GCS Bucket ==="
gcloud storage rsync "$DIST_DIR" "${FRONTEND_BUCKET}" \
  --recursive \
  --delete-unmatched-destination-objects \
  --project="${PROJECT_ID}"

echo ""
echo "=== Setting Cache Headers ==="

# HTML files: no-cache so users always get the latest
gcloud storage objects update "${FRONTEND_BUCKET}/**/*.html" \
  --cache-control="no-cache" \
  --project="${PROJECT_ID}" 2>/dev/null || true
gcloud storage objects update "${FRONTEND_BUCKET}/*.html" \
  --cache-control="no-cache" \
  --project="${PROJECT_ID}" 2>/dev/null || true
echo "  *.html: no-cache"

# Hashed JS and CSS assets: immutable long-lived cache
for ext in js css; do
  gcloud storage objects update "${FRONTEND_BUCKET}/**/*.${ext}" \
    --cache-control="public, max-age=31536000, immutable" \
    --project="${PROJECT_ID}" 2>/dev/null || true
  gcloud storage objects update "${FRONTEND_BUCKET}/*.${ext}" \
    --cache-control="public, max-age=31536000, immutable" \
    --project="${PROJECT_ID}" 2>/dev/null || true
  echo "  *.${ext}: public, max-age=31536000, immutable"
done

echo ""
echo "=== Frontend deployment complete ==="
