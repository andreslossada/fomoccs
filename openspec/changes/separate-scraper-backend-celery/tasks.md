## 1. Celery Infrastructure

- [ ] 1.1 Add `celery` and `redis` dependencies to `backend/pyproject.toml`
- [ ] 1.2 Create `backend/api/celery_app.py` — Celery app config with Redis broker, JSON serialization, task routing (`default` + `geocoding` queues), retry defaults (3 retries, exponential backoff)
- [ ] 1.3 Create task name constants module (e.g., `backend/api/task_names.py`) with `PROCESS_CRAWL_JOB` and `GEOCODE_LOCATION` constants
- [ ] 1.4 Add `REDIS_URL` to `backend/api/config.py` settings with `redis://localhost:6379/0` default
- [ ] 1.5 Verify Celery worker starts: `celery -A backend.api.celery_app worker` discovers tasks and connects to Redis
- [ ] 1.6 Add `celery` and `redis` to `pipeline/pyproject.toml` (lightweight — only for task publishing)

## 2. Geocoding Worker

- [ ] 2.1 Create `backend/api/tasks/geocoding.py` — define `geocode_location(location_id)` Celery task on `geocoding` queue
- [ ] 2.2 Task implementation: fetch location by ID (SQLAlchemy), call existing `geocode_location_name()` from `backend/api/services/geocoding.py`, update lat/lng if within Caracas bounds
- [ ] 2.3 Handle edge cases: no Geoapify results (complete without retry), result outside Caracas (discard coords), API error (retry with backoff)
- [ ] 2.4 Wire geocoding task into location creation — when a new location is created without coords, queue `geocode_location` task instead of inline geocoding
- [ ] 2.5 Test: create location without coords, verify geocoding task runs async and updates coords

## 3. Event Processing Consumer — Location Resolution

- [ ] 3.1 Create `backend/api/services/event_processing.py` with `resolve_location()` function using SQLAlchemy async
- [ ] 3.2 Port location name normalization logic from `pipeline/processor.py` (normalized comparison, alternate name matching)
- [ ] 3.3 Handle new locations: create `Location` record, queue `geocode_location` task
- [ ] 3.4 Handle missing location: log `skipped_no_location` to `ExtractedEventLog`
- [ ] 3.5 Test: extracted event with known location → matches; unknown location → creates + queues geocoding; no location → skipped

## 4. Event Processing Consumer — Tag Processing

- [ ] 4.1 Add `process_tags()` function to `backend/api/services/event_processing.py`
- [ ] 4.2 Port tag rewrite rules logic from `pipeline/processor.py` (pattern → replacement)
- [ ] 4.3 Port tag exclusion rules logic (remove matching tags)
- [ ] 4.4 Port tag removal rules logic (skip entire event if tagged)
- [ ] 4.5 Test: rewrite applied; exclusion removes tag; removal skips event with log entry

## 5. Event Processing Consumer — Short Name & Emoji

- [ ] 5.1 Add `generate_short_name()` function to `backend/api/services/event_processing.py` — port from `pipeline/processor.py`
- [ ] 5.2 Add `extract_emoji()` function — port from `pipeline/processor.py`
- [ ] 5.3 Test: short_name strips redundant location info; emoji extracted from event name

## 6. Event Deduplication & Merging

- [ ] 6.1 Create `backend/api/services/event_merging.py` with core dedup function using SQLAlchemy async
- [ ] 6.2 Port name normalization + significant word extraction from `pipeline/merger.py`
- [ ] 6.3 Port 7-strategy matching: location match + name similarity + date overlap + false positive guards (Men's/Women's, episode numbers, showtimes)
- [ ] 6.4 Port merge logic: merge URLs, keep shorter name + longer description, merge occurrences, create `EventSource` link
- [ ] 6.5 Port event creation: new `Event` + `EventOccurrence` + `EventUrl` + `EventTag` records via SQLAlchemy
- [ ] 6.6 Port event archiving logic: archive events not reported by any source, 14-day grace period for future occurrences
- [ ] 6.7 Port audit logging: write `ExtractedEventLog` entries for every processed event (created, merged, skipped_*)
- [ ] 6.8 Test: new event created; duplicate merged; archive triggers; audit logs written

## 7. Process Crawl Job Task

- [ ] 7.1 Create `backend/api/tasks/processing.py` — define `process_crawl_job(job_id)` Celery task on `default` queue
- [ ] 7.2 Task implementation: fetch extracted events by job_id, call event_processing service (location → tags → short_name → emoji), then call event_merging service (dedup → merge → archive)
- [ ] 7.3 Handle per-source error isolation: if one source fails, mark its `CrawlResult` as failed, continue others
- [ ] 7.4 Handle empty job: no extracted events → log warning, complete successfully
- [ ] 7.5 Update `CrawlResult` status to `processed` for each completed source
- [ ] 7.6 Integration test: end-to-end from extracted events → final events with dedup + geocoding queued

## 8. Scraper Service Isolation

- [ ] 8.1 Add `USE_CELERY` env var support to `pipeline/main.py`
- [ ] 8.2 When `USE_CELERY=true`: after extract phase, publish `process_crawl_job(job_id)` via `send_task()` and stop (skip process + merge stages)
- [ ] 8.3 When `USE_CELERY=false` (default): run full legacy pipeline for backwards compatibility
- [ ] 8.4 Remove processing/merge DB operations from `pipeline/db.py` (move to backend) — only when `USE_CELERY=true` path is verified working
- [ ] 8.5 Remove `processor.py`, `merger.py`, `location_resolver.py` imports from pipeline when legacy path is no longer needed
- [ ] 8.6 Update `pipeline/pyproject.toml` — remove dependencies only needed for processing/merging

## 9. Cleanup & Verification

- [ ] 9.1 Run both paths in parallel: legacy pipeline + Celery consumer on same crawl job, compare final event results
- [ ] 9.2 Remove `USE_CELERY` feature flag and legacy path after verification
- [ ] 9.3 Remove dead code from `pipeline/db.py` (processing/merge functions)
- [ ] 9.4 Remove `pipeline/processor.py`, `pipeline/merger.py`, `pipeline/location_resolver.py`
- [ ] 9.5 Update deployment configs: backend needs Celery worker process, scraper needs `REDIS_URL`
- [ ] 9.6 Verify scraper builds independently with its own `pyproject.toml` (no backend deps)
