# Architecture Deep Dive

## System boundaries

```
┌───────────────────────── Google Cloud Platform ──────────────────────────┐
│                                                                           │
│  Cloud Scheduler              Cloud Run Jobs             Cloud Run Services
│  ┌──────────────┐            ┌──────────────────┐       ┌─────────────────┐
│  │ tier-1 (6h)  │──triggers──│                  │       │ backend-api     │
│  │ tier-2 (12h) │──triggers──│ fomoccs-pipeline │       │ (FastAPI)       │
│  │ tier-3 (24h) │──triggers──│ (Python 3.12)    │       │ port 8080       │
│  └──────────────┘            └────────┬─────────┘       └────────┬────────┘
│                                       │                          │
│                                       │ publishes                │
│                                       ▼                          │
│                              ┌─────────────────┐       ┌────────▼────────┐
│                              │ Redis (Aiven)   │──────▶│ backend-worker  │
│                              │ Celery broker   │       │ (Celery, conc=4)│
│                              └─────────────────┘       └────────┬────────┘
│                                                                  │
└──────────────────────────────────────────────────────────────────┼─────────┘
                                                                   │
                                                     ┌─────────────▼─────────┐
                                                     │  Supabase PostgreSQL   │
                                                     │  (20+ tables)          │
                                                     └───────────────────────┘
```

## Data domains

### Pipeline-owned tables (crawl/extract phase)

| Table | Owner | Purpose |
|-------|-------|---------|
| `sources` | Pipeline | Event source definitions (name, type, tier, disabled) |
| `crawl_configs` | Pipeline | Per-source crawl settings (mode, frequency, browser options) |
| `source_urls` | Pipeline | URLs to crawl per source |
| `crawl_jobs` | Pipeline | Pipeline execution records (started_at, completed, status) |
| `crawl_results` | Pipeline | Per-source crawl status within a job |
| `crawl_contents` | Pipeline | Raw crawled HTML/markdown + LLM-extracted JSON |
| `crawl_url_results` | Pipeline | Per-URL crawl timing/status |
| `crawl_summaries` | Pipeline | Token usage and cost per job |

### Backend-owned tables (processing/merge phase)

| Table | Owner | Purpose |
|-------|-------|---------|
| `extracted_events` | Backend | Structured events parsed from crawl_contents JSON |
| `extracted_event_logs` | Backend | Audit: created/merged/skipped/failed per extraction |
| `events` | Backend | Final deduplicated events (the source of truth) |
| `event_occurrences` | Backend | Date/time instances for each event |
| `event_urls` | Backend | URLs associated with events |
| `event_tags` | Backend | Event ↔ tag associations |
| `event_sources` | Backend | Lineage: which extracted_event → which event |
| `locations` | Backend | Venues with lat/lng coordinates |
| `location_alternate_names` | Backend | Alternate names for location matching |
| `tags` | Backend | Tag definitions |
| `tag_rules` | Backend | Tag rewrite/exclude/remove rules |

## Two handoff modes

The pipeline can hand off crawl_job_id to the backend in two ways:

### Mode A: Celery (production default)
```
Pipeline → Redis broker → backend-worker Celery consumer
```
Set `USE_CELERY=true` and configure `REDIS_URL`. The pipeline publishes `backend.process_crawl_job` task. The worker processes it asynchronously.

### Mode B: Direct HTTP (local/dev)
```
Pipeline → HTTP POST → backend-api /api/v1/admin/process-crawl-job/{id}
```
Set `API_BASE_URL` and `SYNC_API_KEY`. The pipeline directly calls the backend API, which executes the processing synchronously.

## Python version split

| Component | Python | Reason |
|-----------|--------|--------|
| Pipeline | 3.12 | Crawl4AI/Playwright compatibility |
| Backend (API + TUI + Worker) | 3.14 | Latest async features |
| Scripts | 3.12+ | Compatibility with pipeline venv |

## Frontend architecture

The frontend is a **Vanilla JS SPA** (no React/Vue/Svelte framework):

```
src/
├── index.html          # Entry point, loads all JS modules via <script> tags
├── css/                # 9 CSS files (variables, layout, map, tags, popups, etc.)
├── js/
│   ├── core/           # constants, utils, urlParams, historyManager
│   ├── data/           # dataManager (API client), filterManager, searchManager
│   ├── map/            # MapLibre GL wrapper, marker controller, viewport
│   ├── tags/           # Tag filter panel, search, sections, related tags
│   ├── ui/             # Modal, toast, theme switcher, emoji, popups, gestures
│   └── script.js       # Main orchestrator (App.state)
└── data/               # Static config (tags.json, map styles)
```

Build: `node build.js` → reads script tags from index.html → concatenates → esbuild minifies → `dist/`.

Hosted on Vercel (`vercel.json`) or GCS bucket (CI/CD deploys to both).
