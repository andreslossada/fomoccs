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
```

On completion, publishes `PROCESS_CRAWL_JOB` to Redis; backend Celery worker picks it up and handles parsing, dedup, merging, geocoding.
