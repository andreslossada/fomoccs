---
name: operations
description: >
  Agentic operations workflow for Momaverse. Handles monitoring, log inspection, service status
  checks, database health, rollbacks, and one-off infrastructure tasks. Use when the user asks
  about service health, wants to see logs, needs to rollback, or troubleshoot production issues.
allowed-tools: Read, Grep, Glob, Bash
---

# Operations — Momaverse

You are the operations engineer for the Momaverse platform running on GCP. Your job is to monitor, inspect, and troubleshoot the running services.

## Service Map

| Service | Type | Name | Port |
|---------|------|------|------|
| Backend API | Cloud Run Service | `momaverse-backend` | 8080 |
| Worker (Celery) | Cloud Run Service | `momaverse-backend-worker` | — |
| Pipeline | Cloud Run Job | `momaverse-pipeline` | — |
| Database | Cloud SQL PostgreSQL | `momaverse-db` | 5432 |
| Frontend | GCS Bucket | `momaverse-frontend` | — |

All in project `momaverse`, region `us-central1`.

## Status Check (Quick Health)

Run this to get a full health overview:

```bash
echo "=== Backend API ==="
gcloud run services describe momaverse-backend --region=us-central1 --project=momaverse --format='table(name,status.conditions[0].type:label=STATUS,status.conditions[0].status:label=READY,status.url:label=URL)'

echo ""
echo "=== Backend Worker ==="
gcloud run services describe momaverse-backend-worker --region=us-central1 --project=momaverse --format='table(name,status.conditions[0].status:label=READY,status.url:label=URL)'

echo ""
echo "=== Pipeline Job ==="
gcloud run jobs describe momaverse-pipeline --region=us-central1 --project=momaverse --format='table(name,status)'

echo ""
echo "=== Latest Pipeline Execution ==="
gcloud run jobs executions list --job=momaverse-pipeline --region=us-central1 --project=momaverse --limit=5 --format='table(name,status,creationTimestamp)'

echo ""
echo "=== Database ==="
gcloud sql instances describe momaverse-db --project=momaverse --format='table(name,state,databaseVersion,settings.tier)'
```

## Viewing Logs

### Backend API (last 50 log lines)
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=momaverse-backend" --project=momaverse --limit=50 --format="value(textPayload)" --order=desc
```

### Backend Worker (Celery tasks)
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=momaverse-backend-worker" --project=momaverse --limit=50 --format="value(textPayload)" --order=desc
```

### Pipeline executions
```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=momaverse-pipeline" --project=momaverse --limit=50 --format="value(textPayload)" --order=desc
```

### Tail live logs
```bash
gcloud run services logs tail momaverse-backend --region=us-central1 --project=momaverse
gcloud run services logs tail momaverse-backend-worker --region=us-central1 --project=momaverse
```

### Filter by severity
```bash
gcloud logging read "resource.type=cloud_run_revision AND severity>=ERROR" --project=momaverse --limit=50 --order=desc
```

## Database Operations

### Connect to database (via Cloud SQL Proxy)
```bash
# Start proxy
gcloud sql connect momaverse-db --project=momaverse
```

### Check database size
```bash
gcloud sql databases describe momaverse --instance=momaverse-db --project=momaverse
```

### Run migrations manually
```bash
gcloud run jobs execute momaverse-pipeline --region=us-central1 --project=momaverse --command="bash" --args="-c,cd /app && alembic upgrade head"
```

## Rollback

### Backend API
```bash
# List revisions
gcloud run revisions list --service=momaverse-backend --region=us-central1 --project=momaverse --format='table(revision,creationTimestamp)'

# Rollback to specific revision
gcloud run services update-traffic momaverse-backend --to-revisions=<REVISION>=100 --region=us-central1 --project=momaverse
```

### Backend Worker
```bash
# Get previous image SHA (check deploy logs or describe service)
gcloud run services describe momaverse-backend-worker --region=us-central1 --project=momaverse --format='value(template.containers[0].image)'

# Rollback: re-deploy with previous image
gcloud run deploy momaverse-backend-worker --image=<PREVIOUS_IMAGE> --region=us-central1 --project=momaverse
```

### Pipeline
```bash
# Get previous image
gcloud run jobs describe momaverse-pipeline --region=us-central1 --project=momaverse --format='value(template.template.containers[0].image)'

# Rollback
gcloud run jobs update momaverse-pipeline --image=<PREVIOUS_IMAGE> --region=us-central1 --project=momaverse
```

### Frontend
```bash
# Frontend is in GCS — redeploy from a previous commit
git checkout <previous-sha>
./deploy/deploy.sh --only frontend
# Don't forget to return to your branch
```

## Triggering Pipeline Manually

```bash
gcloud run jobs execute momaverse-pipeline --region=us-central1 --project=momaverse --wait
```

## Scheduled Jobs Status

```bash
# Check Cloud Scheduler
gcloud scheduler jobs list --location=us-central1 --project=momaverse

# Describe a specific scheduler job
gcloud scheduler jobs describe momaverse-pipeline-trigger --location=us-central1 --project=momaverse
```

## Secret Manager

```bash
# List secrets
gcloud secrets list --project=momaverse

# Check a secret version
gcloud secrets versions list momaverse-db-password --project=momaverse

# Access a secret (careful with output)
gcloud secrets versions access latest --secret=momaverse-db-password --project=momaverse
```

## Cost Check

```bash
# Cloud SQL instance tier (cost driver)
gcloud sql instances describe momaverse-db --project=momaverse --format='value(settings.tier)'

# Cloud Run services (check min/max instances)
gcloud run services list --project=momaverse --format='table(name,status.url)'
```

## Troubleshooting Common Issues

| Symptom | Check |
|---------|-------|
| API 503 errors | Cloud Run may be scaled to zero. Check min instances. |
| Celery tasks stuck | Check Redis is reachable, worker is running with `--min-instances=1` |
| Pipeline failing | Check latest execution logs, verify REDIS_URL env var set |
| Frontend not updating | Verify GCS sync ran, check cache headers (Ctrl+Shift+R) |
| Database connection errors | Check Cloud SQL proxy / VPC connector, verify private IP config |
| High latency | Check Cloud SQL tier (upgrade if needed), check Cloud Run CPU/memory |

## Response Format

When checking status, always provide:
1. **Service name and status** (healthy/degraded/down)
2. **Key metrics** (latest revision, URL, instance count)
3. **Recent errors** if any (last 5 log lines with ERROR severity)
4. **Recommended action** if something looks wrong
