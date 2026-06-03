#!/usr/bin/env bash
#
# One-time bootstrap: creates Workload Identity Federation resources
# so GitHub Actions can authenticate to GCP.
#
# Run this ONCE before setting up CI/CD workflows.
# After this, all infrastructure changes go through GitHub Actions.
#
# Safe to re-run — skips resources that already exist.
#
# Usage: ./bootstrap-wif.sh
#
# After terraform apply creates the frontend bucket, run:
#   gsutil iam ch "serviceAccount:fomoccs-cicd@fomoccs.iam.gserviceaccount.com:roles/storage.objectAdmin" \
#     "gs://fomoccs-frontend"

set -euo pipefail

PROJECT_ID="fomoccs"
POOL_ID="fomoccs-github-pool"
PROVIDER_ID="github"
SA_NAME="fomoccs-cicd"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO="Anedu91/fomoccs"

echo "=== Enabling required APIs ==="
gcloud services enable iam.googleapis.com --project="${PROJECT_ID}" --quiet
gcloud services enable iamcredentials.googleapis.com --project="${PROJECT_ID}" --quiet

echo "=== Creating CI/CD service account ==="
gcloud iam service-accounts create "${SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="Fomoccs CI/CD (GitHub Actions)" 2>/dev/null || echo "  (already exists)"

echo "=== Creating Workload Identity Pool ==="
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --display-name="GitHub Actions Pool" \
  --description="OIDC federation for GitHub Actions" 2>/dev/null || echo "  (already exists)"

echo "=== Creating OIDC Provider (restricted to ${REPO}) ==="
gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com" 2>/dev/null || echo "  (already exists)"

echo "=== Getting project number ==="
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")

echo "=== Allowing GitHub pool to impersonate CI/CD SA ==="
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${REPO}" \
  --quiet

echo "=== Granting CI/CD SA permissions ==="
# Push Docker images to Artifact Registry
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/artifactregistry.writer" \
  --quiet

# Deploy Cloud Run services and jobs
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.admin" \
  --quiet

# Act as backend/pipeline service accounts when deploying
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet

# NOTE: Frontend bucket storage permission must be added AFTER terraform apply
# creates the bucket. See the command in the script header.

echo ""
echo "=== Done! ==="
echo ""
echo "Workload Identity Provider:"
echo "  projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo ""
echo "Service Account:"
echo "  ${SA_EMAIL}"
echo ""
echo "Add these GitHub Secrets (Settings → Secrets → Actions):"
echo "  GCP_PROJECT_ID = ${PROJECT_ID}"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo ""
echo "After 'terraform apply' creates the frontend bucket, run:"
echo "  gsutil iam ch \"serviceAccount:${SA_EMAIL}:roles/storage.objectAdmin\" \"gs://fomoccs-frontend\""
