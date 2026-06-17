# Deployment

## Components and where they run

| Component | Where | How |
|-----------|-------|-----|
| **Pipeline** | Cloud Run Job | Docker image → Artifact Registry → triggered by Cloud Scheduler |
| **Backend API** | Cloud Run Service | Docker image → Artifact Registry → continuous deployment |
| **Backend Worker** | Cloud Run Service | Same image as API, different entrypoint (Celery worker) |
| **Frontend** | GCS bucket | Static files with content-hash URLs, CDN-cached |
| **Terraform** | Local / CI | `infrastructure/` defines all GCP resources |

## Infrastructure (Terraform)

```bash
cd infrastructure

# First time: bootstrap Workload Identity Federation
./bootstrap-wif.sh

# Plan changes
terraform plan

# Apply
terraform apply
```

Terraform manages:
- `google_cloud_run_v2_service` — backend-api + backend-worker
- `google_cloud_run_v2_job` — fomoccs-pipeline
- `google_cloud_scheduler_job` — 3 cadences (tier 1/2/3)
- `google_sql_database_instance` — Cloud SQL PostgreSQL
- `google_storage_bucket` — Frontend hosting
- `google_secret_manager_secret` — API keys, DB passwords
- `google_vpc_access_connector` — VPC for Cloud Run → Cloud SQL

## CI/CD (GitHub Actions)

`.github/workflows/ci-cd.yml`:

1. **On push to any branch:** change detection via `dorny/paths-filter`
2. **Parallel test jobs:** backend (ruff + mypy + pytest), pipeline (pytest), frontend (build check)
3. **On push to main:** conditional deploy
   - Backend: `docker build && push` → `gcloud run deploy`
   - Pipeline: `docker build && push` → `gcloud run jobs update`
   - Frontend: `npm run build` → `gsutil rsync` to GCS

## Manual deployment

```bash
# Everything
./deploy/deploy.sh

# Specific components
./deploy/deploy-backend.sh
./deploy/deploy-pipeline.sh
./deploy/deploy-frontend.sh

# Set up Cloud Scheduler cadences
./deploy/setup-scheduler.sh
```

## Environment variables

See `.env.example` for the complete list. Key variables:

| Variable | Component | Purpose |
|----------|-----------|---------|
| `OPENCODE_GO_API_KEY` | Pipeline | Primary LLM provider |
| `GEMINI_API_KEY` | Pipeline | Fallback LLM |
| `REDIS_URL` | Pipeline + Worker | Celery broker connection |
| `DATABASE_URL` | All | Supabase PostgreSQL connection |
| `API_BASE_URL` | Pipeline | Direct HTTP handoff target |
| `SYNC_API_KEY` | Pipeline + Backend | API key for handoff endpoint |
| `GOOGLE_MAPS_API_KEY` | Backend | Geocoding (primary) |
| `GEOAPIFY_API_KEY` | Backend | Geocoding (fallback) |

## Docker images

### Backend (`backend/Dockerfile`)
- Base: `python:3.14-slim`
- Multi-stage: uv sync → slim runtime
- Entrypoint: uvicorn (API) or celery (worker)

### Pipeline (`pipeline/Dockerfile`)
- Base: `python:3.12-slim`
- Multi-stage: uv sync + Chromium + Xvfb
- Entrypoint: `docker-entrypoint.sh` (starts Xvfb, runs main.py)
- Larger image due to Chromium (~1.5GB)
