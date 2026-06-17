# Adding a New Source

Step-by-step guide to add a new event website to the pipeline.

## 1. Figure out the source type

### Browser mode (most common)
For HTML/JavaScript websites. The pipeline renders the page in a headless
Chromium browser and extracts events via LLM.

Use when:
- Events are listed on a web page you can visit
- The page has visible text (not just flyer images)
- JavaScript rendering is needed

### JSON API mode
For structured API endpoints. Skips browser rendering and LLM extraction —
maps JSON fields directly to events.

Use when:
- The source has a REST API returning JSON
- The JSON structure is predictable
- You don't want to pay for LLM extraction

### Instagram mode (profile scraping)

For Instagram profiles that post event announcements:

Use when:
- An Instagram account regularly posts event flyers or announcements
- The captions contain event details (date, venue, description)
- You have an Instagram cookies file for authentication

```bash
fomoccs-tui
# Press 's' for Sources → 'n' for New Source
# Select crawl_mode: "instagram"
# Fill Instagram username and max posts
```

Or via the API:

```json
POST /api/v1/sources/
{
  "name": "El Gallo Cinefilo",
  "type": "primary",
  "urls": [],
  "crawl_config": {
    "crawl_mode": "instagram",
    "crawl_frequency": 1,
    "json_api_config": {
      "username": "elgallocinefilo",
      "max_posts": 20
    }
  }
}
```

**Instagram config fields (in json_api_config):**

| Field | Default | Description |
|-------|---------|-------------|
| `username` | required | Instagram username to scrape (without @) |
| `max_posts` | 20 | Maximum posts to harvest per crawl |

**Requirements:**
- Instagram cookies file at `~/.config/fomoccs/instagram_cookies.txt` (Netscape format)
- Or set `INSTAGRAM_COOKIES_PATH` env var
- Pipeline Docker image must have Xvfb + Playwright Chromium (already included)

**How it works:**
1. Pipeline launches Playwright browser with Instagram cookies
2. Navigates to the profile, scrolls to discover posts
3. For each post, opens the overlay and extracts caption, date, and location
4. Formats everything as readable text
5. The same LLM extractor processes the text to extract structured events

### Vision mode (flyer images)
For Instagram-style sources where events are posted as images.

Use when:
- Events are flyer images with no accessible text
- Set `process_images: true` in crawl_config

## 2. Create the source via TUI

```bash
fomoccs-tui
# Press 's' for Sources → 'n' for New Source
```

Or via the API:

```bash
curl -X POST http://localhost:8000/api/v1/sources/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "name": "My New Venue",
    "type": "primary",
    "urls": [{"url": "https://example.com/events", "sort_order": 1}],
    "crawl_config": {
      "crawl_mode": "browser",
      "crawl_frequency": 7
    }
  }'
```

## 3. Choose a tier

| Tier | Crawl frequency | Throttle | Use for |
|------|----------------|----------|---------|
| 1 | Every 6 hours | 0.5s | Ticketing, popular venues, aggregators |
| 2 | Every 12 hours | 2.0s | News-driven, medium-traffic sites |
| 3 | Every 24 hours | 5.0s | Slow-changing, captcha-prone sites |

Default: tier 2.

## 4. Tune crawl config

### Page doesn't load all events? → Enable scrolling

```json
"scan_full_page": true,
"delay_before_return_html": 8
```

### "Load More" button? → Click-to-load

```json
"selector": ".load-more-btn",
"num_clicks": 5
```

### Site has bot detection? → Stealth mode

```json
"use_stealth": true,
"text_mode": false,
"light_mode": false
```

### Events on detail pages? → Deep crawl

```json
"keywords": "evento, show, agenda",
"max_pages": 20
```

The crawler will find all links on the main page whose URL contains any
keyword, follow them, and combine the content.

### Events are flyer images? → Vision mode

```json
"process_images": true
```

### URL has month/year? → Templates

```json
{"url": "https://example.com/events/{{month}}-{{year}}"}
```

Templates: `{{month}}`, `{{year}}`, `{{next_month}}`, `{{next_month_year}}`

## 5. Test the source

```bash
cd pipeline
python main.py --ids <source_id>
```

Watch the output for:
- `source_complete` with `event_count > 0` → working
- `source_complete` with `event_count = 0` → LLM didn't find events (check content)
- `source_error` → crawl or extraction failed (check error message)
- `host_backoff` → site rate-limited us (increase tier or add delay)

## 6. Add default tags (optional)

```json
PUT /api/v1/sources/{id}/config
{"default_tags": ["música", "cultura"]}
```

All events from this source will automatically get these tags.

## 7. Add extraction notes (optional)

```json
PUT /api/v1/sources/{id}/config
{"notes": "Events have format: 'Artist — Venue — Time'. Description is in second paragraph."}
```

These notes are passed to the LLM as part of the extraction prompt, helping it
parse the source's specific format.

## 8. Common issues

| Symptom | Likely fix |
|---------|-----------|
| 0 events extracted | Increase `delay_before_return_html`, check content isn't empty |
| 0 chars crawled | Set `scan_full_page: true`, check if site blocks headless browsers |
| Wrong dates | Add extraction notes with date format hint |
| Duplicate events | Check location matching — add alternate names if venue name differs |
| LLM hallucinates events | Reduce extraction scope, add more specific notes |
| Rate limited (429) | Increase tier, add `min_request_interval_seconds` |
