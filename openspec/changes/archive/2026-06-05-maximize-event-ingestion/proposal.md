## Why

The platform currently pulls 13 events across 11 locations, almost all from a single source (MakeTicket). To get to thousands of monthly events the pipeline must (a) run more sources in parallel, (b) add official venue calendars and ticketing platforms with near-zero ban risk, and (c) stay inside LLM and geocoding rate limits without getting Cloud Run's egress IP banned from any one domain.

This change sets up the source tiering, parallel execution, and per-domain throttling that the rest of the ingestion work depends on.

## What Changes

- **Add 8-12 new sources** spanning three risk tiers (official venues, ticketing platforms, aggregator sites). Each source is added to the `sources` table with `tier=1|2|3` metadata and conservative crawl defaults.
- **Parallelize pipeline** so `python main.py --ids=1,2,3,...` runs sources concurrently via `asyncio.gather` with a bounded semaphore (default 5), instead of one source at a time.
- **Per-domain throttling** — the crawler enforces a configurable request rate per hostname (e.g. 1 req / 2s for Tier 1, 1 req / 5s for Tier 2) so we don't trip bot defenses.
- **Geocoding dedup** — `resolve_location()` reuses an existing `Location` row when the normalized name matches, skipping the geocoding API call.
- **Schedule** — Cloud Scheduler jobs run the pipeline on staggered cadences (6h for ticketing, 12-24h for venue calendars, 24h for Tier 2).
- **Tune existing 3 sources** (MakeTicket, Goliiive, El Diario) with `keywords`, longer `delay_before_return_html`, and `scan_full_page=true` to lift their yield from 11 to 35+ events/run.
- **No new models, no LLM chain changes, no schema migrations**. Sources, locations, events tables are unchanged.

## Capabilities

### New Capabilities

- `source-tiering`: Classifies sources by ban risk (T1: official venues, T2: ticketing platforms, T3: stealth-required) and binds crawl defaults to each tier.
- `pipeline-parallelization`: `main.py` processes multiple sources concurrently with `asyncio.gather` + bounded semaphore; per-source failures don't block siblings.
- `crawl-throttling`: Per-hostname rate limiter inside the crawler; respects `Retry-After` and backs off on 403/429.
- `geocoding-dedup`: Pre-geocoding name normalization + lookup against the `locations` table to avoid redundant API calls; preserves the `GeocodingKeyDep` chain (Google → Geoapify).
- `ingestion-monitoring`: Structured logs (event_count, status_code, llm_provider, geocode_provider) so a dashboard can spot rising 403/429 rates and degrading yield per source.

### Modified Capabilities

None. The proposal introduces new behavior only; no existing spec-level requirements change.

## Impact

- **Pipeline** (`pipeline/main.py`, `pipeline/crawler.py`): parallel orchestration + throttling state.
- **Backend** (`backend/api/services/event_processing.py`): `resolve_location()` gains a dedup lookup.
- **DB**: no schema changes. Two metadata columns may be added to `sources` (`tier` smallint, `crawl_concurrency` smallint) — done in a non-breaking way via Alembic.
- **Infra**: 2-3 new Cloud Scheduler jobs for the staggered cadences; existing `fomoccs-pipeline` job unchanged.
- **Costs**: <$10/month at projected run rate (Google Places free tier + LLM free tier + Cloud Run job minutes).
- **No new external services** beyond what is already enabled (Google Places + Geocoding APIs).
