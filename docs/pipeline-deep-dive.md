# Pipeline Deep Dive

How events go from a website to your database, step by step.

## Overview

The pipeline has exactly **3 phases**:

1. **Crawl** — fetch content from source websites
2. **Extract** — run LLM chain to extract structured events
3. **Handoff** — publish job to backend for processing

Post-extraction work (dedup, merge, geocoding, tag rules) happens in the backend
Celery worker. The pipeline does NOT do any of that.

## Phase 1: Crawl

Entry point: `pipeline/main.py → run_pipeline()`

### Step 0: Resume incomplete jobs

Before crawling anything, the pipeline checks for `crawl_results` that are stuck
in "crawled" status (meaning: content was fetched but LLM extraction never ran
or failed). These get re-queued as `extract_only` items.

### Step 1: Find due sources

```sql
SELECT sources.* + urls + crawl_config
FROM sources
WHERE disabled = false
  AND deleted_at IS NULL
  AND (last_crawled_at IS NULL OR last_crawled_at + crawl_frequency < now())
ORDER BY tier ASC, last_crawled_at ASC NULLS FIRST
```

Sources are split by crawl mode:
- **JSON API sources** → crawled first (fast, no browser needed)
- **Browser sources** → crawled second (need Chromium)

### Step 2: Streaming workers

The pipeline uses `PIPELINE_CONCURRENCY` (default 2) async workers. Each worker
pulls items from a shared queue and processes them:

```
Worker 1: [json_api_source_A] [browser_source_C] [extract_only_X]
Worker 2: [json_api_source_B] [browser_source_D] ...
         ↑ crawl + extract are interleaved within each worker
```

This means while Worker 1 is waiting for the LLM to extract events from
source_A, Worker 2 can be crawling source_B's website. Maximum throughput.

#### JSON API mode (`crawler.py → crawl_json_api()`)

```python
# 1. Fetch JSON from source's URL
response = httpx.get(url)
data = response.json()

# 2. Navigate data_path (e.g., "results.events")
for part in data_path.split("."):
    data = data[part]

# 3. Filter by date window
events = [e for e in data if date_within_window(e, date_window_days)]

# 4. Map fields directly — no LLM needed
extracted = [map_json_to_event(e) for e in events]
```

JSON API mode skips LLM entirely. Events are created from structured JSON
with direct field mapping. Currently optimized for Alternativa Teatral's API.

#### Browser mode (`crawler.py → crawl_source()`)

```python
# 1. Create Crawl4AI browser with config matching source settings
config = get_browser_config(text_mode, light_mode, use_stealth)

# 2. Crawl each URL for the source
for url in source_urls:
    result = await browser.arun(
        url=url,
        scan_full_page=True,     # scroll to load lazy content
        delay_before_return_html=5,  # wait for JS
        js_code=source.js_code,       # optional custom JS
    )
    content += result.markdown

# 3. If keywords configured, follow matching links
if source.keywords:
    for link in find_matching_links(content, keywords):
        result = await browser.arun(url=link)
        content += result.markdown

# 4. Save to crawl_contents.crawled_content
```

Browser groups: sources with same `(text_mode, light_mode, use_stealth)` share
a browser instance, reducing startup overhead.

## Phase 2: Extract

Entry point: `pipeline/extractor.py → extract_events()`

### The LLM provider chain

The extractor tries providers in priority order. If one fails (429 rate limit,
413 TPM exceeded, timeout), it falls through to the next:

```
1. OpenCode Go DeepSeek V4 Flash  ← primary (subscription, no RPM cap)
      ↓ 429/413/timeout
2. Gemini 2.5 Flash               ← 5 RPM / 20 RPD free tier
      ↓ 429/413/timeout
3. Gemini 2.5 Flash-Lite          ← 1,500 RPD free tier
      ↓ 429/413/timeout  
4. OpenRouter (configurable model) ← optional
      ↓ 429/413/timeout
5. Groq (Llama 3.3 70B)           ← 14,400 RPD free
      ↓ 429/413/timeout
6. xAI Grok                        ← optional
      ↓
   AllProvidersExhausted → job retries later
```

Rate limit delays:
- OpenCode Go: `GEMINI_RATE_LIMIT_DELAY` (default 0.6s)
- Gemini Flash: 13s (5 RPM)
- Gemini Flash-Lite: 3.5s (20 RPM)

### Two-pass extraction for large pages

If crawled content suggests >50 events, a two-pass strategy is used:

**Pass 1 — Simple schema:**
```
Extract: name, location_name, start_date, end_date, url
Cost: ~1 token per character of content
```

**Pass 2 — Enrichment (batched, max_batches configurable):**
```
For each batch of events:
  Extract: description, tags, hashtags, emoji
Cost: only the events in the batch, not the full page
```

This prevents the LLM from hallucinating or truncating on very large pages.
The `max_batches` config limits cost (default: 3 batches).

### Vision extraction (`process_images=true`)

For sources where events are posted as flyer images:

```python
# 1. Find images in crawled content
images = extract_image_urls(content)

# 2. Download each image
for img_url in images:
    img_data = httpx.get(img_url).content

# 3. Base64 encode and send to multimodal LLM
    events = await llm.chat(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract events from this flyer..."},
                {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]
        }]
    )
```

### Extraction output format

The LLM is instructed to output JSON matching this schema:

```json
{
  "events": [
    {
      "name": "Concierto de Jazz",
      "description": "Noche de jazz en vivo...",
      "location_name": "El Teatro",
      "start_date": "2026-06-20",
      "end_date": "2026-06-20",
      "start_time": "20:00",
      "url": "https://...",
      "tags": ["música", "jazz"],
      "hashtags": ["#JazzCCS"],
      "emoji": "🎷"
    }
  ]
}
```

This JSON is stored in `crawl_contents.extracted_content` and later parsed by
`backend/api/services/event_processing.py`.

## Phase 3: Handoff

### Token usage tracking

Every LLM call records: provider, model, input_tokens, output_tokens, cost.
Aggregated into `crawl_summaries` table per job.

### Publishing to backend

Two modes (controlled by env vars):

**Celery mode (production):**
```python
# pipeline/celery_publisher.py
celery_app.send_task("backend.process_crawl_job", args=[crawl_job_id])
```
→ Redis broker → backend-worker picks it up

**Direct HTTP mode (local):**
```python
POST /api/v1/admin/process-crawl-job/{crawl_job_id}
Header: X-API-Key: {SYNC_API_KEY}
```
→ backend-api processes it synchronously

### What happens in the backend

When the backend receives a `crawl_job_id`:

```
backend/api/tasks/processing.py → _process_crawl_job()

For each crawl_result in the job:
  1. Parse extracted_content JSON → create ExtractedEvent rows
  2. Resolve location (match existing or create new)
  3. Apply tag rules (rewrite/exclude/remove)
  4. Generate short_name + emoji

Then:
  5. Merge extracted_events into final events table (dedup by name+location+date)
  6. Queue geocode_location Celery task for any new locations
```

The merge step in `backend/api/services/event_merging.py` handles:
- Name normalization (lowercase, stemming, common word removal)
- Semantic equivalents ("Teatro" ≈ "Teatro Municipal")
- Location + date matching
- Archiving outdated events (marking old occurrences as archived)

## Throttling

`crawler.py → HostnameThrottle` enforces per-domain pacing:

| Tier | Interval | Use case |
|------|----------|----------|
| 1 | 0.5s | Ticketing sites, popular venues |
| 2 | 2.0s | News-driven, slower sites |
| 3 | 5.0s | Captcha-prone, slow sites |

Override: `sources.min_request_interval_seconds` per source.
429/403 response → 30s cooldown (or `Retry-After` header value).

## Structured logging

Every source emits a JSON line to stdout. Cloud Logging auto-parses these:

```json
{"event": "source_complete", "source_id": 42, "event_count": 15, "duration_ms": 4500}
{"event": "source_error", "source_id": 99, "error_type": "Timeout", "error": "..."}
{"event": "host_backoff", "hostname": "example.com", "cooldown_seconds": 60}
```

Query examples in [INSTRUCTIONS.md](../INSTRUCTIONS.md).
