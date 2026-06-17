# Event Processing Pipeline

Crawls event websites, extracts structured data with Gemini AI, and merges deduplicated events into the database.

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL
- [uv](https://docs.astral.sh/uv/) (package manager)

### Install Dependencies

```bash
cd pipeline
uv sync
```

### Environment Variables

Create a `.env` file in the project root:

```env
FOMO_ENV=local          # "local" or "production"

# LLM Provider Chain (in priority order)
# OpenCode Go DeepSeek V4 Flash — primary, subscription-based, NO rate limits
OPENCODE_GO_API_KEY="your-api-key"
# Gemini fallbacks (free tier: 5/20/1500 RPM/RPD depending on model)
GEMINI_API_KEY="your-api-key"
GEMINI_MODEL="gemini-2.5-flash"            # default
GEMINI_MODEL_LITE="gemini-2.5-flash-lite"  # high-RPD fallback

# Optional additional providers (fall through if all above exhausted):
OPENROUTER_CRAWLER_API_KEY="..."     # OpenRouter (Gemini/other models)
OPENROUTER_MODEL="google/gemini-2.5-flash"
GROQ_API_KEY="..."                   # Groq (Llama 3.3 70B)
XAI_API_KEY="..."                    # xAI Grok

# Extraction settings
GEMINI_TIMEOUT=120                   # seconds per LLM call
GEMINI_RATE_LIMIT_DELAY=0.6          # min seconds between calls (OpenCode Go has no RPM cap)
PIPELINE_CONCURRENCY=2               # concurrent crawl+extract workers

# Production DB (only needed if FOMO_ENV=production)
PROD_DB_HOST="..."
PROD_DB_NAME="..."
PROD_DB_USER="..."
PROD_DB_PASS="..."
```

## LLM Provider Chain

The pipeline uses a multi-provider fallback chain for event extraction (defined in `pipeline/extractor.py`). Providers are tried in priority order:

1. **OpenCode Go DeepSeek V4 Flash** — primary, subscription with generous/no rate limits. Set `OPENCODE_GO_API_KEY`.
2. **Gemini 2.5 Flash** — best quality fallback, 5 RPM / 20 RPD on free tier.
3. **Gemini 2.5 Flash-Lite** — same API key, 1,500 RPD (75x more quota).
4. **OpenRouter** — separate provider, configurable model.
5. **Groq** — very fast inference, 14,400 RPD free tier (Llama 3.3 70B).
6. **xAI Grok** — separate provider.

When a provider hits 429 (rate limit) or 413 (TPM exceeded), it goes into cooldown and the next provider is tried. `RateLimitError` (429) and `BadRequestError` with status 413 both trigger fallback. All providers exhausted → `AllProvidersExhausted` exception → job retries later.

**Rate limit delays** are configured per provider:
- OpenCode Go: `GEMINI_RATE_LIMIT_DELAY=0.6` (primary, no RPM cap)
- Gemini Flash: hardcoded 13s (5 RPM)
- Gemini Flash-Lite: hardcoded 3.5s (20 RPM)

The extraction step tracks token usage per provider in `crawl_summaries`.

## Usage

```bash
# Run full pipeline (all sources due for crawling)
uv run main.py

# Run specific source by ID
uv run main.py --ids 4

# Run multiple sources
uv run main.py --ids 4,12,25

# Limit to first N due sources
uv run main.py --limit 5
```

When `--ids` is used, `crawl_frequency` is ignored — those sources are always crawled.

## Pipeline Steps (3-step flow)

The pipeline **only crawls and extracts**. Post-extraction processing (dedup, merge,
location resolution, geocoding, tag rules) happens in the backend Celery worker.

```
STEP 0: Resume incomplete crawl results from prior runs
STEP 1: Find sources due for crawling (respects crawl_frequency + tier)
STEP 2: Streaming crawl + extract (browser or JSON API) — workers interleave
        crawling and LLM extraction for maximum throughput
  HANDOFF: Publish crawl_job_id to backend via Celery (or direct HTTP)
           → backend/api/tasks/processing.py picks it up
           → backend/api/services/event_processing.py enriches events
           → backend/api/services/event_merging.py deduplicates into final table
           → backend/api/tasks/geocoding.py geocodes new locations
```

### Data Flow

```
sources + source_urls + crawl_configs
     ↓
[Crawl / pipeline/crawler.py] → crawl_contents.crawled_content
     ↓
[Extract / pipeline/extractor.py] → crawl_contents.extracted_content (JSON)
     ↓
[HANDOFF — Celery task or direct HTTP POST]
     ↓
[Process / backend/api/services/event_processing.py] → extracted_events
     ↓
[Merge / backend/api/services/event_merging.py] → events + event_occurrences
                                                    + event_urls + event_tags
                                                    + event_sources + locations
```

## Module Structure

```
pipeline/                          # Crawl + LLM extraction only
├── main.py                        # Orchestrator (crawl → extract → handoff)
├── crawler.py                     # Crawl4AI browser + JSON API crawler
├── extractor.py                   # Multi-provider LLM extraction chain
├── db.py                          # Pipeline DB helpers (psycopg2)
├── celery_publisher.py            # Thin Celery publisher (Redis broker)
├── task_names.py                  # Shared task-name constants
├── Dockerfile                     # Multi-stage build (Chromium + Xvfb)
└── tests/

backend/api/                       # Post-extraction processing (FastAPI + Celery)
├── services/
│   ├── event_processing.py        # Parse extracted JSON → create extracted_events rows
│   ├── event_merging.py           # Deduplicate extracted_events → merge into events table
│   ├── geocoding.py               # Google Places + Geoapify geocoding
│   └── tags.py                    # Tag rule application
├── tasks/
│   ├── processing.py              # Celery task: process_crawl_job
│   └── geocoding.py               # Celery task: geocode_location
└── task_names.py                  # Task name constants (mirrored in pipeline/task_names.py)
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `sources` | Event source websites |
| `crawl_configs` | Crawl settings per source (1:1 with sources) |
| `source_urls` | URLs to crawl per source (1:many) |
| `crawl_jobs` | Pipeline execution records |
| `crawl_results` | Status tracking per source per job |
| `crawl_contents` | Raw HTML/markdown + extracted JSON |
| `extracted_events` | Structured events from Gemini (before dedup) |
| `extracted_event_logs` | Audit log (created/merged/skipped/failed) |
| `events` | Final deduplicated events |
| `event_occurrences` | Date/time instances |
| `event_urls` | URLs associated with events |
| `event_tags` | Event ↔ tag associations |
| `event_sources` | Lineage: which extracted_event → which event |
| `locations` | Venues with coordinates |
| `location_alternate_names` | Alternate names for location matching |
| `tags` | Tag definitions |
| `tag_rules` | Tag rewrite/exclude/remove rules |

---

## Sources API

Sources are managed through the REST API at `/api/v1/sources`.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/sources/` | List all sources (paginated) |
| `GET` | `/api/v1/sources/{id}` | Get source with URLs and crawl config |
| `POST` | `/api/v1/sources/` | Create source (with URLs and config in one request) |
| `PUT` | `/api/v1/sources/{id}` | Update source name/type/disabled |
| `DELETE` | `/api/v1/sources/{id}` | Soft delete source |
| `PUT` | `/api/v1/sources/{id}/config` | Create or update crawl config |
| `POST` | `/api/v1/sources/{id}/urls` | Add a URL |
| `DELETE` | `/api/v1/sources/{id}/urls/{url_id}` | Remove a URL |

### Creating a Browser Mode Source

For HTML/JavaScript websites. Uses [Crawl4AI](https://github.com/unclecode/crawl4ai) to render pages in a browser.

```json
POST /api/v1/sources/
{
  "name": "My Event Site",
  "type": "primary",
  "urls": [
    {"url": "https://example.com/events", "sort_order": 1}
  ],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7
  }
}
```

### Creating a JSON API Source

For structured API endpoints that return JSON directly. Skips browser rendering and Gemini extraction — maps fields directly to events.

```json
POST /api/v1/sources/
{
  "name": "My API Source",
  "type": "primary",
  "urls": [],
  "crawl_config": {
    "crawl_mode": "json_api",
    "crawl_frequency": 3,
    "json_api_config": {
      "base_url": "https://api.example.com/events",
      "data_path": "results.events",
      "date_window_days": 30,
      "jsonp_callback": null,
      "fields_include": null
    }
  }
}
```

**json_api_config fields:**

| Field | Description |
|-------|-------------|
| `base_url` | API endpoint URL |
| `data_path` | Dot-separated path to events in response (e.g., `"data.events"`) |
| `date_window_days` | Only include events within N days from today (default: 30) |
| `jsonp_callback` | JSONP wrapper function name to strip (optional) |
| `fields_include` | Array of field names to keep (optional, keeps all if null) |

### Updating Crawl Config

Use `PUT /api/v1/sources/{id}/config` to update any config field. Only send the fields you want to change:

```json
PUT /api/v1/sources/1/config
{
  "scan_full_page": true,
  "delay_before_return_html": 8
}
```

### Adding URLs

```json
POST /api/v1/sources/1/urls
{
  "url": "https://example.com/events/page/2",
  "sort_order": 2
}
```

URLs with per-URL JavaScript:

```json
POST /api/v1/sources/1/urls
{
  "url": "https://example.com/calendar",
  "js_code": "document.querySelector('.next-month').click();",
  "sort_order": 1
}
```

---

## Crawl Config Options

All fields in `crawl_configs` with their purpose and defaults:

### Scheduling

| Field | Default | Description |
|-------|---------|-------------|
| `crawl_frequency` | 7 | Days between crawls |
| `force_crawl` | false | Force crawl on next run (resets to false after) |
| `crawl_after` | null | Don't crawl until this date (for seasonal sources) |

### Browser Rendering

| Field | Default | Description |
|-------|---------|-------------|
| `text_mode` | true | Disable images for faster text-only crawls |
| `light_mode` | true | Minimal browser features for speed |
| `use_stealth` | false | Undetected browser mode for bot detection bypass |
| `javascript_enabled` | true | Enable JavaScript execution |

Sources with different `text_mode`/`light_mode`/`use_stealth` values get separate browser instances.

### Page Interaction

| Field | Default | Description |
|-------|---------|-------------|
| `scan_full_page` | true | Scroll the entire page before capturing (loads lazy content) |
| `scroll_delay` | 0.2 | Seconds to pause between scroll steps |
| `delay_before_return_html` | 5 | Seconds to wait for JS to finish after page load |
| `remove_overlay_elements` | false | Remove popups/cookie banners that obscure content |
| `crawl_timeout` | 120 | Total timeout in seconds for the entire crawl |

### Click-to-Load Pagination

For sites with "Load More" buttons:

| Field | Default | Description |
|-------|---------|-------------|
| `selector` | null | CSS selector for the pagination/load-more button |
| `num_clicks` | 2 | Number of times to click the button |

Example: A site with a "Show More Events" button:

```json
PUT /api/v1/sources/1/config
{
  "selector": ".load-more-btn",
  "num_clicks": 5
}
```

The crawler generates JavaScript that clicks the button N times with 1-second delays between clicks.

### Custom JavaScript

For complex page interactions beyond simple button clicks:

| Field | Default | Description |
|-------|---------|-------------|
| `js_code` | null | Custom JavaScript to execute before content capture |

This overrides `selector`/`num_clicks` if set. Can also be set per-URL via `js_code` in the URL payload.

Source-level JS (applies to all URLs):

```json
PUT /api/v1/sources/1/config
{
  "js_code": "document.querySelector('.tab-future').click(); await new Promise(r => setTimeout(r, 2000));"
}
```

Per-URL JS (overrides source-level for this URL only):

```json
POST /api/v1/sources/1/urls
{
  "url": "https://example.com/calendar",
  "js_code": "document.querySelector('.month-next').click();",
  "sort_order": 1
}
```

### Deep Crawling with Keywords

For sites where events are spread across multiple pages linked from a listing:

| Field | Default | Description |
|-------|---------|-------------|
| `keywords` | null | Comma-separated URL patterns to follow (e.g., `"event, show, concert"`) |
| `max_pages` | 30 | Maximum pages to crawl when following links |

**How it works:**
1. Crawls the main URL
2. Finds all links on the page
3. Follows links whose URL contains any keyword (wildcard match: `*event*`)
4. Scrapes each matched page
5. Combines all content for extraction

**When to use:** When the listing page only shows titles/dates, but full event details are on individual pages.

**When NOT to use:** When all event info is already visible on the listing page (like Indie Hoy's `/eventos/`).

Example:

```json
PUT /api/v1/sources/1/config
{
  "keywords": "event, show, concert",
  "max_pages": 20
}
```

### Content Filtering

| Field | Default | Description |
|-------|---------|-------------|
| `content_filter_threshold` | null | Pruning filter aggressiveness, 0.0–1.0. Null = disabled |

When set, applies a `PruningContentFilter` that removes boilerplate content (nav, footer, ads). Higher values = more aggressive filtering. Start with `0.5` and adjust.

### Extraction Settings

| Field | Default | Description |
|-------|---------|-------------|
| `process_images` | false | Use Gemini vision model to extract events from flyer images |
| `max_batches` | 3 | Max enrichment batches for large pages (limits API cost) |
| `notes` | null | Extra instructions passed to Gemini in the extraction prompt |

### Tags

| Field | Default | Description |
|-------|---------|-------------|
| `default_tags` | null | Array of tags automatically added to all events from this source |

---

## Source URLs

Each source can have multiple URLs. They're crawled sequentially and their content is combined.

Add URLs individually after creating the source:

```json
POST /api/v1/sources/1/urls
{"url": "https://example.com/events", "sort_order": 1}

POST /api/v1/sources/1/urls
{"url": "https://example.com/events/page/2", "sort_order": 2}
```

Or include them in the initial `POST /api/v1/sources/` request (see Creating a Browser Mode Source above).

### URL Templates

URLs support date placeholders resolved at crawl time:

| Template | Resolves to | Example |
|----------|-------------|---------|
| `{{month}}` | Current month (lowercase) | `march` |
| `{{year}}` | Current year | `2026` |
| `{{next_month}}` | Next month name | `april` |
| `{{next_month_year}}` | Year of next month | `2026` |

```json
POST /api/v1/sources/1/urls
{"url": "https://example.com/events/{{month}}-{{year}}", "sort_order": 1}
// Resolves to: https://example.com/events/march-2026
```

---

## Common Source Configurations

Each example shows a full `POST /api/v1/sources/` request body.

### Simple static page

All events are listed on one page, no JavaScript needed:

```json
{
  "name": "Simple Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7
  }
}
```

### JavaScript-heavy site with lazy loading

Events load as you scroll:

```json
{
  "name": "Lazy Load Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7,
    "scan_full_page": true,
    "delay_before_return_html": 8
  }
}
```

### Site with "Load More" button

```json
{
  "name": "Paginated Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7,
    "selector": "button.load-more",
    "num_clicks": 5
  }
}
```

### Site with bot detection

```json
{
  "name": "Protected Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7,
    "use_stealth": true,
    "text_mode": false,
    "light_mode": false
  }
}
```

### Event listing with detail pages

Listing page links to individual event pages:

```json
{
  "name": "Blog-Style Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7,
    "keywords": "evento, show, agenda",
    "max_pages": 20
  }
}
```

### Image flyers (no text content)

Events are posted as flyer images:

```json
{
  "name": "Flyer Venue",
  "type": "primary",
  "urls": [{"url": "https://example.com/events", "sort_order": 1}],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7,
    "process_images": true
  }
}
```

### Monthly calendar with URL templates

```json
{
  "name": "Calendar Venue",
  "type": "primary",
  "urls": [
    {"url": "https://example.com/calendar/{{month}}-{{year}}", "sort_order": 1},
    {"url": "https://example.com/calendar/{{next_month}}-{{next_month_year}}", "sort_order": 2}
  ],
  "crawl_config": {
    "crawl_mode": "browser",
    "crawl_frequency": 7
  }
}
```

---

## Tag Rules

Control how tags are processed across all sources. Managed via `/api/v1/tag-rules`.

```json
// Rewrite: normalize tag names
POST /api/v1/tag-rules/
{"rule_type": "rewrite", "pattern": "standup", "replacement": "Comedy"}

// Exclude: silently remove a tag
POST /api/v1/tag-rules/
{"rule_type": "exclude", "pattern": "Lorem"}

// Remove: skip the entire event if it has this tag
POST /api/v1/tag-rules/
{"rule_type": "remove", "pattern": "Cancelled"}
```

## Troubleshooting

### Source returns 0 pages / 0 chars

- **Check `scan_full_page`**: If null, set to `true` — the page may need scrolling to load content.
- **Check `keywords`**: If set, the crawler follows links instead of scraping the main page. Remove keywords if all events are already on the listing page.
- **Check `delay_before_return_html`**: Increase if the page needs more time to render JavaScript.

### Bot detection / verification page

Set `use_stealth = true`. Also set `text_mode = false` and `light_mode = false` — minimal browser features can trigger detection.

### Content too small (under 500 chars)

The pipeline skips extraction if crawled content is under 500 bytes (prevents Gemini hallucinations). Check if the site requires JavaScript, scrolling, or button clicks to reveal content.

### Events not matching existing locations

The processor tries multiple matching strategies (exact name, substring, alternate names, address, short name). If a venue isn't matching, add alternate names via the locations API:

```json
PUT /api/v1/locations/42
{"alternate_names": ["The Venue Formerly Known As..."]}
```

### Duplicate events being created

Check if the location matches. Events are deduplicated by location + date + similar name. If the same venue has two location records, events won't merge. Consolidate locations and add alternate names.
