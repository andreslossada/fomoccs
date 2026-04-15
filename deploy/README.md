# Deployment

Manual deployment scripts for the Momaverse project on GCP.

## Architecture

The project has two deployment phases that run independently:

1. **Infrastructure** (Terraform) — manages GCP resources: Cloud Run services, Cloud SQL, GCS buckets, IAM, networking
2. **Application** (`deploy.sh`) — builds and deploys code to the resources Terraform created

Infrastructure must exist before application code can be deployed. After the initial setup, you only need to re-run Terraform when infrastructure changes.

## Deployment Order

```
1. Infrastructure (if changed)    →  terraform apply
2. Backend                        →  Docker build/push → Cloud Run service update
3. Pipeline                       →  Docker build/push → Cloud Run job update
4. Frontend                       →  npm build → GCS sync + cache headers
```

`deploy.sh` enforces the order 2→3→4 automatically.

## Infrastructure (Terraform)

Managed separately from application deploys. Run from the `infrastructure/` directory:

```bash
cd infrastructure
terraform init
terraform plan          # review changes
terraform apply         # apply after reviewing
```

Always review the plan before applying — Terraform changes can destroy or modify production resources.

## Application (deploy.sh)

The orchestrator detects what changed and deploys only affected components.

```bash
# Auto-detect changes from last commit
./deploy/deploy.sh

# Deploy specific components
./deploy/deploy.sh --only backend
./deploy/deploy.sh --only backend,frontend

# Deploy everything
./deploy/deploy.sh --all

# Diff between specific commits
./deploy/deploy.sh --from abc123 --to def456
```

### Component Scripts

Each script can also run standalone with env vars:

| Script | What it does |
|--------|-------------|
| `deploy-backend.sh` | Builds Docker image, pushes to Artifact Registry, updates Cloud Run service |
| `deploy-pipeline.sh` | Builds Docker image, pushes to Artifact Registry, updates Cloud Run job |
| `deploy-frontend.sh` | Runs `npm build` with API URL injection, syncs to GCS, sets cache headers |

### Change Detection

When run without flags, `deploy.sh` diffs `HEAD~1..HEAD` and maps changed paths to components:

| Path pattern | Component |
|-------------|-----------|
| `backend/` | backend |
| `pipeline/` | pipeline |
| `src/`, `build.js`, `package.json` | frontend |
| `deploy/deploy-*.sh` | matching component |

## Prerequisites

- `gcloud` CLI — authenticated and configured
- `docker` — running (for backend/pipeline)
- `npm` — installed (for frontend)
- `terraform` — installed (for infrastructure)

## Rollback

Each deploy script prints a rollback command after a successful deploy. Example:

```
Rollback: gcloud run services update-traffic momaverse-backend --to-revisions=momaverse-backend-00042-abc=100 --region=us-central1 --project=momaverse
```

For frontend, re-run the deploy from a previous commit:

```bash
git checkout <previous-sha>
./deploy/deploy.sh --only frontend
```

## Celery Worker

The pipeline and backend are connected through Redis as a Celery broker:

```
Pipeline Cloud Run Job (momaverse-pipeline)
  → publishes backend.process_crawl_job tasks to Redis via REDIS_URL
  → Backend worker Cloud Run Service (momaverse-backend-worker)
      consumes tasks using api.celery_app
```

The backend worker runs the same Docker image as the API but overrides the entrypoint to run Celery instead of Uvicorn. It uses `--no-cpu-throttling` and `--min-instances=1` so it stays warm and can process tasks without cold-start delays.

### Required before deploying

`REDIS_URL` must be set in your shell (or CI environment) before running any deploy script:

```bash
export REDIS_URL="redis://<host>:<port>/0"
./deploy/deploy.sh --only backend
./deploy/deploy.sh --only pipeline
```

### Rollback

For the worker, re-deploy the previous image SHA:

```bash
gcloud run deploy momaverse-backend-worker \
  --image=<previous-image> \
  --region=us-central1 \
  --project=momaverse
```

## Environment Variables

These are set by `deploy.sh` and passed to component scripts. Override for standalone use:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | `momaverse` | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `DOCKER_REPO` | `us-central1-docker.pkg.dev/momaverse/momaverse-docker` | Artifact Registry path |
| `BACKEND_SERVICE` | `momaverse-backend` | Cloud Run service name |
| `BACKEND_WORKER_SERVICE` | `momaverse-backend-worker` | Cloud Run service name for Celery worker |
| `PIPELINE_JOB` | `momaverse-pipeline` | Cloud Run job name |
| `FRONTEND_BUCKET` | `gs://momaverse-frontend` | GCS bucket for frontend |
| `REDIS_URL` | *(required)* | Redis broker URL for Celery (no default — must be set) |
