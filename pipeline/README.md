# Scraper Pipeline

Crawl event sources, extract structured event data, hand off to backend via Celery.

Post-extract processing (dedup, merge, location resolution, geocoding) lives in the backend. This pipeline only crawls + extracts, then publishes a `PROCESS_CRAWL_JOB` task.

## Pipeline Overview

`main.py` orchestrates:

1. **Crawl** — query `sources` table for sites due for crawling, store content in `crawl_results` / `crawl_contents`.
2. **Extract** — call OpenRouter (Gemini) to extract structured events from crawled content.
3. **Handoff** — mark `crawl_jobs` complete and publish a Celery task with `crawl_job_id` for the backend worker.

## Module Structure

```
pipeline/
├── main.py              # Orchestrator
├── db.py                # Scraper-only DB helpers (psycopg2)
├── crawler.py           # Crawl4AI + JSON API crawler
├── extractor.py         # OpenRouter/Gemini event extraction
├── celery_publisher.py  # Thin Celery publisher (Redis broker)
├── task_names.py        # Shared task-name constants (mirrors backend)
└── tests/
    ├── test_crawler.py
    ├── test_celery_publisher.py
    └── test_main_celery_bridge.py
```

## Key Tables

```
sources               - Event sources to crawl
crawl_configs         - Per-source crawl settings
source_urls           - URLs per source (with per-URL js_code)
crawl_jobs            - Pipeline run records
crawl_results         - Status + timestamps per (job, source)
crawl_contents        - crawled_content + extracted_content (1:1 with crawl_results)
crawl_summaries       - Token usage per crawl job
```

Post-handoff tables (`events`, `event_occurrences`, `locations`, etc.) are owned by the backend.

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL
- Redis (broker for Celery handoff)

Deps managed with `uv` — see `pyproject.toml`.

### Configuration

`.env` variables:

```env
FOMO_ENV=local                         # or 'production'
OPENROUTER_CRAWLER_API_KEY=...
OPENROUTER_MODEL=google/gemini-2.5-flash
EXTRACTION_TIMEOUT=120
REDIS_URL=redis://localhost:6379/0
```

Production DB creds read from `PROD_DB_HOST`, `PROD_DB_NAME`, `PROD_DB_USER`, `PROD_DB_PASS`.

## Usage

```bash
python main.py                     # all sources due
python main.py --ids 941           # specific source(s)
python main.py --ids 941,942       # multiple
python main.py --limit 5           # cap
python main.py --tier 1            # force-process all active tier-1 sources
                                  #   (used by Cloud Scheduler cadences)
```

On completion, publishes `PROCESS_CRAWL_JOB` to Redis; backend Celery worker picks it up and handles parsing, dedup, merging, geocoding.

## Tiering & Throttling

Sources carry a `tier` (1/2/3) in the `sources` table. The pipeline enforces
per-hostname throttling via `HostnameThrottle` in `crawler.py`:

| Tier | Default interval | Use case                            |
|------|------------------|-------------------------------------|
| 1    | 0.5 s            | Ticketing, popular venues, aggregators (crawled every 6h)  |
| 2    | 2.0 s            | News-driven, slower-changing sites (crawled every 12h)    |
| 3    | 5.0 s            | Captcha-prone, slow sites (crawled every 24h)            |

A per-source `min_request_interval_seconds` override (set in the `sources`
row) wins over the tier default. After a 429/403, `HostnameThrottle` puts
the hostname in a 30s cooldown (or longer if the server returns
`Retry-After`). The throttle is shared across all `PIPELINE_CONCURRENCY`
(default 5) workers in a single run.

## Structured Logs

Each source emits a JSON line to stdout at the end of its pipeline.
Cloud Logging auto-parses these as `jsonPayload` fields.

Events emitted by `pipeline/crawler.py` and `pipeline/extractor.py`:

- `event=source_complete` — successful crawl + extract. Includes
  `source_id`, `source_name`, `mode` (`browser` or `json_api`),
  `crawl_result_id`, `urls_crawled`, `duration_ms`, `content_bytes`,
  `event_count` (from extraction), `llm_provider`, `geocode_provider`,
  `geocode_hit`.
- `event=source_error` — failed crawl or extract. Includes `source_id`,
  `mode`, `duration_ms`, `error_type` (e.g. `EmptyContent`, `Timeout`),
  `error` (message), `crawl_result_id`.
- `event=source_extracted` / `event=source_extract_error` — finer-grained
  events from the extractor.
- `event=host_backoff` — emitted when a hostname enters cooldown
  (Retry-After or 429). Includes `hostname`, `reason`, `cooldown_seconds`.

### Cloud Logging queries (Logs Explorer → Query)

**Find degraded sources (crawled OK but produced 0 events):**

```
jsonPayload.event="source_complete" AND jsonPayload.event_count=0
```

**Find sources that errored in the last 24h:**

```
jsonPayload.event="source_error" AND timestamp>="2026-06-04T00:00:00Z"
```

**Find rate-limited hostnames:**

```
jsonPayload.event="host_backoff"
```

**Per-tier event throughput (last 7 days):**

```
jsonPayload.event="source_complete" AND jsonPayload.tier=1
```

(Use `tier=2` or `tier=3` for other tiers — the field is set by the
caller via the `--tier` flag in `main.py`.)

**Largest single-source crawls (capacity planning):**

```
jsonPayload.event="source_complete" AND jsonPayload.content_bytes>100000
```

## Burn-in checklist (1 week)

After enabling the new tiered scheduler (see `deploy/setup-scheduler.sh`):

1. **Day 1-2**: confirm each tier scheduler job is firing on its cadence.
   Query: `protoPayload.methodName="google.cloud.scheduler.v1.CloudScheduler.RunJob"`
2. **Day 2-3**: monitor `event=source_error` rate. Targets: < 10% of source
   runs should error; > 10% means a source needs tuning (delay bump,
   stealth=true, tier promotion to T2).
3. **Day 3-4**: monitor `event=host_backoff` rate. A spike in backoffs
   means a site is responding to our crawler IP; consider tier promotion
   or proxying through residential.
4. **Day 4-5**: review `event=source_complete AND event_count=0` set.
   Each entry is a source that crawled but yielded no events — usually
   a prompt-tuning issue or a site redesign.
5. **Day 5-7**: review geocoding spend. The backend logs every
   geocoding call (`jsonPayload.event="geocoding.complete"`) — sum
   `cost_usd` and check it stays under the Google Places free tier
   (~$200/mo credit).
6. **Day 7**: archive this change with `npx -y @fission-ai/openspec
   archive maximize-event-ingestion`.
