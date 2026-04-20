#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# === Topology ===
# scraper (Cloud Run Job: momaverse-pipeline)
#   → publishes backend.process_crawl_job tasks to Redis via REDIS_URL
#   → backend worker (Cloud Run Service: momaverse-backend-worker) consumes via api.celery_app
#   → backend API (Cloud Run Service: momaverse-backend) serves HTTP traffic

# === Shared Constants ===
export PROJECT_ID="momaverse"
export REGION="us-central1"
export DOCKER_REPO="us-central1-docker.pkg.dev/momaverse/momaverse-docker"
export BACKEND_SERVICE="momaverse-backend"
export BACKEND_WORKER_SERVICE="${BACKEND_WORKER_SERVICE:-momaverse-backend-worker}"
export PIPELINE_JOB="momaverse-pipeline"
export FRONTEND_BUCKET="gs://momaverse-frontend"

# === Required Env Vars ===
: "${REDIS_URL:?REDIS_URL must be set — required by pipeline (publish) and backend worker (consume)}"
export REDIS_URL

# === Pre-flight Checks ===
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: required command not found: $1" >&2
    exit 1
  }
}

require_cmd git

# === Parse Arguments ===
FROM=""
TO=""
ONLY=""
ALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)
      FROM="$2"
      shift 2
      ;;
    --to)
      TO="$2"
      shift 2
      ;;
    --only)
      ONLY="$2"
      shift 2
      ;;
    --all)
      ALL=true
      shift
      ;;
    *)
      echo "Usage: $0 [--from SHA] [--to SHA] [--only component[,component]] [--all]"
      echo ""
      echo "Components: backend, pipeline, frontend"
      echo ""
      echo "Examples:"
      echo "  $0                              # auto-detect changes (HEAD~1..HEAD)"
      echo "  $0 --from abc123 --to def456    # diff between specific commits"
      echo "  $0 --only backend               # force deploy specific component"
      echo "  $0 --only backend,frontend      # force deploy multiple components"
      echo "  $0 --all                        # force deploy everything"
      echo ""
      echo "Note: Infrastructure (Terraform) is managed separately via terraform CLI."
      exit 1
      ;;
  esac
done

# === Determine What to Deploy ===
DEPLOY_BACKEND=false
DEPLOY_PIPELINE=false
DEPLOY_FRONTEND=false

if [[ "$ALL" == true ]]; then
  DEPLOY_BACKEND=true
  DEPLOY_PIPELINE=true
  DEPLOY_FRONTEND=true
elif [[ -n "$ONLY" ]]; then
  IFS=',' read -ra COMPONENTS <<< "$ONLY"
  for comp in "${COMPONENTS[@]}"; do
    case "$comp" in
      backend)  DEPLOY_BACKEND=true ;;
      pipeline) DEPLOY_PIPELINE=true ;;
      frontend) DEPLOY_FRONTEND=true ;;
      *)
        echo "Error: unknown component '$comp'" >&2
        echo "Valid components: backend, pipeline, frontend" >&2
        exit 1
        ;;
    esac
  done
else
  # Auto-detect changes via git diff
  FROM="${FROM:-HEAD~1}"
  TO="${TO:-HEAD}"

  git rev-parse --verify "${FROM}^{commit}" >/dev/null 2>&1 || {
    echo "Error: invalid commit reference: $FROM" >&2
    exit 1
  }
  git rev-parse --verify "${TO}^{commit}" >/dev/null 2>&1 || {
    echo "Error: invalid commit reference: $TO" >&2
    exit 1
  }

  echo "=== Detecting changes between ${FROM} and ${TO} ==="
  CHANGED_FILES=$(git diff --name-only --diff-filter=ACDMRT "$FROM" "$TO")

  if [[ -z "$CHANGED_FILES" ]]; then
    echo "No changes detected. Nothing to deploy."
    exit 0
  fi

  while IFS= read -r file; do
    case "$file" in
      backend/*)                   DEPLOY_BACKEND=true ;;
      pipeline/*)                  DEPLOY_PIPELINE=true ;;
      src/*|build.js|package.json) DEPLOY_FRONTEND=true ;;
      deploy/deploy-backend.sh)    DEPLOY_BACKEND=true ;;
      deploy/deploy-pipeline.sh)   DEPLOY_PIPELINE=true ;;
      deploy/deploy-frontend.sh)   DEPLOY_FRONTEND=true ;;
      deploy/deploy.sh)            ;; # orchestrator change, no component to flag
    esac
  done <<< "$CHANGED_FILES"
fi

# === Dirty Working Tree Warning ===
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  echo "Warning: working tree has uncommitted changes; image tags will use current HEAD SHA."
fi

# === Check Required Tools for Flagged Components ===
if [[ "$DEPLOY_BACKEND" == true ]] || [[ "$DEPLOY_PIPELINE" == true ]]; then
  require_cmd docker
  require_cmd gcloud
fi
if [[ "$DEPLOY_FRONTEND" == true ]]; then
  require_cmd npm
  require_cmd gcloud
fi

# === Summary ===
echo ""
echo "=== Deploy Summary ==="
echo "  Backend:        $DEPLOY_BACKEND"
echo "  Pipeline:       $DEPLOY_PIPELINE"
echo "  Frontend:       $DEPLOY_FRONTEND"
echo ""

if [[ "$DEPLOY_BACKEND" != true ]] && \
   [[ "$DEPLOY_PIPELINE" != true ]] && [[ "$DEPLOY_FRONTEND" != true ]]; then
  echo "No components flagged for deployment. Nothing to deploy."
  exit 0
fi

# === Execute Component Scripts in Order ===
if [[ "$DEPLOY_BACKEND" == true ]]; then
  echo "=== Deploying Backend ==="
  "$SCRIPT_DIR/deploy-backend.sh"
fi

if [[ "$DEPLOY_PIPELINE" == true ]]; then
  echo "=== Deploying Pipeline ==="
  "$SCRIPT_DIR/deploy-pipeline.sh"
fi

if [[ "$DEPLOY_FRONTEND" == true ]]; then
  echo "=== Deploying Frontend ==="
  "$SCRIPT_DIR/deploy-frontend.sh"
fi

echo ""
echo "=== Deploy Complete ==="
