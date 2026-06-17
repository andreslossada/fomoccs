# FomoCCS — Event Discovery Platform (Caracas)

Crawls event websites, extracts structured data with LLMs, and serves an interactive map of events in Caracas.

## Architecture

```mermaid
flowchart LR
    subgraph GCP["☁️ Google Cloud Platform"]
        Scheduler["Cloud Scheduler\n(3 cadences: 6h/12h/24h)"]
        subgraph Pipeline["Pipeline Job (Cloud Run Job)"]
            Crawler["crawler.py\nCrawl4AI browser + JSON API"]
            Extractor["extractor.py\nMulti-provider LLM chain\n(OpenCode Go → Gemini → ...)"]
            Crawler --> Extractor
        end
        Redis["Redis (Aiven)\nCelery broker"]
        Worker["Cloud Run Service\nbackend-worker\n(Celery worker, concurrency=4)"]
        API["Cloud Run Service\nbackend-api\n(FastAPI)"]
    end

    subgraph DB["🗄️ Supabase PostgreSQL"]
        Sources["sources + crawl_configs\n+ source_urls"]
        PipelineTables["crawl_jobs + crawl_results\n+ crawl_contents + crawl_summaries"]
        Events["events + event_occurrences\n+ event_tags + event_sources\n+ locations"]
    end

    Frontend["🌐 Frontend\n(Vanilla JS + MapLibre GL)\nVercel / GCS"]
    TUI["🖥️ fomoccs-tui\n(Textual terminal UI)\nAdmin panel"]

    Scheduler -->|"triggers"| Pipeline
    Pipeline -->|"reads/writes"| Sources
    Pipeline -->|"writes"| PipelineTables
    Pipeline -->|"publishes task"| Redis
    Redis -->|"consumes"| Worker
    Worker -->|"processes + merges"| Events
    Worker -->|"reads"| PipelineTables
    API -->|"reads"| Events
    Frontend -->|"GET /api/v1/feed/events"| API
    TUI -->|"direct DB + API calls"| API
    TUI -->|"direct DB"| DB

    style Pipeline fill:#4a9,stroke:#296
    style Worker fill:#48e,stroke:#26c
    style API fill:#48e,stroke:#26c
    style Frontend fill:#e84,stroke:#c62
    style TUI fill:#e84,stroke:#c62
```

## Components

| Component | Directory | Tech | Purpose |
|-----------|-----------|------|---------|
| **Pipeline** | `pipeline/` | Python 3.12, Crawl4AI, LLMs | Crawl event websites, extract structured events via multi-provider LLM chain |
| **Backend API** | `backend/api/` | Python 3.14, FastAPI, SQLAlchemy | REST API for frontend, post-extraction processing (dedup, merge, geocoding) |
| **Backend Worker** | `backend/api/tasks/` | Celery + Redis | Async processing: parse extracted events, deduplicate, geocode |
| **Admin TUI** | `backend/tui/` | Python 3.14, Textual | Terminal admin panel: manage sources, events, locations, tag rules |
| **Frontend** | `src/` | Vanilla JS, MapLibre GL | Interactive map of Caracas with event filtering, dark/light theme |
| **Infrastructure** | `infrastructure/` | Terraform | GCP: Cloud Run, Cloud SQL, Artifact Registry, Cloud Scheduler, GCS |

## Quick Links

- [Architecture deep dive](docs/architecture.md)
- [Pipeline deep dive](docs/pipeline-deep-dive.md)
- [Adding a new source](docs/adding-a-source.md)
- [TUI guide](docs/tui-guide.md)
- [Deployment](docs/deployment.md)
- [Source configuration reference](INSTRUCTIONS.md)

## Local Development

```bash
# Backend (API + TUI)
cd backend && uv sync
fomoccs-tui                    # Launch admin TUI

# Pipeline (requires Chromium)
cd pipeline && uv sync
python main.py --ids 123       # Crawl specific source

# Frontend
npm install && npm run build   # Build to dist/
npm run dev                    # Dev server

# Infrastructure
cd infrastructure
terraform init && terraform plan
```
