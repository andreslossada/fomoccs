---
name: deploy
description: >
  Agentic deployment workflow for Momaverse. Handles the full deploy pipeline: detect changes,
  run tests, build Docker images, push to Artifact Registry, update Cloud Run services/jobs,
  sync frontend to GCS, and verify health. Use when the user asks to deploy any component
  (backend, pipeline, frontend), deploy everything, or deploy specific changes.
allowed-tools: Read, Grep, Glob, Bash
---

# Deploy — Momaverse

You are deploying the Momaverse platform to GCP. Follow these steps in order.

## Pre-flight

Before deploying, check:

1. **Prerequisites**: Verify these tools are available:
   ```bash
   git --version && gcloud --version && docker --version && npm --version
   ```

2. **REDIS_URL**: Must be set. Check with `echo $REDIS_URL`. If empty, ask the user.

3. **gcloud auth**: Verify authenticated:
   ```bash
   gcloud auth print-access-token --project=momaverse > /dev/null 2>&1 && echo "Authenticated" || echo "NOT authenticated — run: gcloud auth login && gcloud auth application-default login"
   ```

4. **Working tree**: Check for uncommitted changes:
   ```bash
   git status --porcelain
   ```
   Warn the user if there are uncommitted changes — they'll be included in the Docker build.

## Step 1 — Determine what to deploy

Ask the user what they want to deploy:

- **All components** (full deploy): `./deploy/deploy.sh --all`
- **Specific component**: `./deploy/deploy.sh --only <backend|pipeline|frontend>`
- **Auto-detect changes**: `./deploy/deploy.sh` (diffs HEAD~1..HEAD)
- **Specific commit range**: `./deploy/deploy.sh --from <sha> --to <sha>`

If the user doesn't specify, run change detection:
```bash
git diff --name-only HEAD~1..HEAD
```
And recommend which components to deploy based on changed paths:
- `backend/` → backend
- `pipeline/` → pipeline
- `src/`, `build.js`, `package.json` → frontend

## Step 2 — Run tests (for backend/pipeline changes)

If deploying backend or pipeline:
```bash
cd backend && uv run pytest
```

If tests fail, STOP and report failures before deploying.

## Step 3 — Deploy

Run the appropriate deploy command:

```bash
# Full deploy (all components)
cd <project_root> && ./deploy/deploy.sh --all

# Specific components
cd <project_root> && ./deploy/deploy.sh --only backend
cd <project_root> && ./deploy/deploy.sh --only backend,frontend

# Auto-detect
cd <project_root> && ./deploy/deploy.sh
```

The deploy script will:
1. **Backend**: Docker build → push to Artifact Registry → deploy Cloud Run API service → deploy Cloud Run worker service
2. **Pipeline**: Docker build → push → update Cloud Run Job
3. **Frontend**: npm ci → fetch backend URL → npm build → sync dist/ to GCS → set cache headers

## Step 4 — Verify

After deploy, verify each deployed component:

### Backend API health check
```bash
BACKEND_URL=$(gcloud run services describe momaverse-backend --region=us-central1 --project=momaverse --format='value(status.url)')
curl -s "${BACKEND_URL}/health" | head -20
```

### Backend Worker status
```bash
gcloud run services describe momaverse-backend-worker --region=us-central1 --project=momaverse --format='value(status.url,status.conditions)'
```

### Pipeline job status
```bash
gcloud run jobs describe momaverse-pipeline --region=us-central1 --project=momaverse --format='value(name,status)'
```

### Frontend
```bash
gcloud storage ls gs://momaverse-frontend/index.html --project=momaverse
```

## Step 5 — Report

After deploy, display a summary:

```
## Deploy Complete

| Component | Status | Revision/Image | URL |
|-----------|--------|---------------|-----|
| Backend API | ✓ | <sha> | <url> |
| Backend Worker | ✓ | <sha> | <url> |
| Pipeline | ✓ | <sha> | — |
| Frontend | ✓ | <sha> | <bucket_url> |

### Rollback Instructions
<rollback commands from deploy output>
```

## What gets deployed where

| Component | Build | Deploy Target | Command |
|-----------|-------|---------------|---------|
| Backend API | Docker (backend/Dockerfile) | Cloud Run: momaverse-backend | `deploy/deploy-backend.sh` |
| Backend Worker | Same Docker image | Cloud Run: momaverse-backend-worker (celery entrypoint) | `deploy/deploy-backend.sh` |
| Pipeline | Docker (pipeline/Dockerfile) | Cloud Run Job: momaverse-pipeline | `deploy/deploy-pipeline.sh` |
| Frontend | esbuild via npm run build | GCS: momaverse-frontend | `deploy/deploy-frontend.sh` |

## Handling failures

| Failure | Action |
|---------|--------|
| Docker build fails | Check Dockerfile, fix issue, re-run |
| Docker push fails | Check Artifact Registry auth and permissions |
| Cloud Run deploy fails | Check service exists, check IAM permissions |
| npm build fails | Check build.js and package.json |
| GCS sync fails | Check bucket exists and permissions |
| Health check fails | Check logs: `gcloud run services logs tail momaverse-backend --region=us-central1` |

Always offer to troubleshoot or rollback if something fails.
