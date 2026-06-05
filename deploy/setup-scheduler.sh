#!/usr/bin/env bash
#
# Creates (or updates) 3 Cloud Scheduler jobs that trigger the
# ``fomoccs-pipeline`` Cloud Run Job at cadence per tier:
#
#   - ingest-tier1 : every 6 hours, processes all active tier-1 sources
#   - ingest-tier2 : every 12 hours, processes all active tier-2 sources
#   - ingest-tier3 : every 24 hours, processes all active tier-3 sources
#
# The scheduler hits the pipeline with ``--tier N`` so the database query
# in ``db.get_sources_due_for_crawling`` returns every active source at
# that tier regardless of last_crawled_at.
#
# Requires:
#   - gcloud auth (Application Default Credentials or gcloud auth login)
#   - The pipeline Cloud Run Job ``fomoccs-pipeline`` already deployed
#   - The Cloud Run Invoker role granted to the scheduler service account
#     (default service account is
#     ``PROJECT_NUMBER-compute@developer.gserviceaccount.com``)

set -euo pipefail

: "${PROJECT_ID:=fomoccs-caracas}"
: "${REGION:=us-central1}"
: "${PIPELINE_JOB:=fomoccs-pipeline}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
: "${SERVICE_ACCOUNT:=${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Error: gcloud CLI not found" >&2
  exit 1
fi

create_job() {
  local name="$1"
  local schedule="$2"
  local tier="$3"

  if gcloud scheduler jobs describe "$name" \
      --location="$REGION" \
      --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  Updating scheduler job: $name ($schedule, tier=$tier)"
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --schedule="$schedule" \
      --time-zone="UTC" \
      --http-method=POST \
      --uri="${RUN_JOB_URI}" \
      --oauth-service-account-email="$SERVICE_ACCOUNT" \
      --update-headers="Content-Type=application/json" \
      --message-body="{\"overrides\":{\"containerOverrides\":[{\"args\":[\"python\",\"main.py\",\"--tier=${tier}\"]}]}}" \
      --description="Fomoccs pipeline: tier ${tier} sources"
  else
    echo "  Creating scheduler job: $name ($schedule, tier=$tier)"
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --schedule="$schedule" \
      --time-zone="UTC" \
      --http-method=POST \
      --uri="${RUN_JOB_URI}" \
      --oauth-service-account-email="$SERVICE_ACCOUNT" \
      --headers="Content-Type=application/json" \
      --message-body="{\"overrides\":{\"containerOverrides\":[{\"args\":[\"python\",\"main.py\",\"--tier=${tier}\"]}]}}" \
      --description="Fomoccs pipeline: tier ${tier} sources"
  fi
}

echo "=== Setting up Cloud Scheduler jobs (project: $PROJECT_ID) ==="

# Cloud Run Jobs run API URL. The path is:
#   /apis/run.googleapis.com/v1/namespaces/{PROJECT_ID}/jobs/{JOB}:run
# Use the project ID in the namespace (not the project number).
RUN_JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${PIPELINE_JOB}:run"

# Tier 1: every 6 hours (00, 06, 12, 18 UTC)
create_job "fomoccs-ingest-tier1" "0 */6 * * *" 1

# Tier 2: every 12 hours (02, 14 UTC)
create_job "fomoccs-ingest-tier2" "0 */12 * * *" 2

# Tier 3: every 24 hours at 04 UTC
create_job "fomoccs-ingest-tier3" "0 4 * * *" 3

echo ""
echo "=== Scheduler setup complete ==="
echo "  fomoccs-ingest-tier1  schedule='0 */6 * * *'   tier=1"
echo "  fomoccs-ingest-tier2  schedule='0 */12 * * *'  tier=2"
echo "  fomoccs-ingest-tier3  schedule='0 4 * * *'     tier=3"
echo ""
echo "  Trigger manually with:"
echo "    gcloud scheduler jobs run fomoccs-ingest-tier1 --location=$REGION --project=$PROJECT_ID"
