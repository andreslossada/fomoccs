## Context

Today the pipeline processes one source at a time, sequentially, inside a single Cloud Run Job execution. We have 5 sources registered, 3 active, and 13 events in the feed. Reaching thousands of events/month means more sources AND faster per-run throughput, while respecting per-domain rate limits so a single aggressive crawl doesn't get our egress IP banned.

The LLM and geocoding chains are already in place and stable. The work here is on the *control plane* around the pipeline: how many sources run in parallel, how fast each source hits its target domain, and how we keep both manageable from a single config knob.

## Goals / Non-Goals

**Goals**

- Run all sources in a single job execution concurrently (bounded), so adding sources does not linearly increase wall time.
- Per-domain request throttling to stay under each site's tolerance.
- Source risk tiering (T1/T2/T3) so configuration choices (delay, stealth, cadence) default sensibly.
- Reuse existing locations to avoid burning the Google Places free tier.
- Surface enough telemetry to back off automatically when a source starts returning 403/429.

**Non-Goals**

- Changing the LLM chain, the geocoding chain, or the DB schema in any breaking way.
- Adding paid residential proxies (Instagram, Facebook) — those are a future change if at all.
- Real-time ingestion (webhooks, push APIs). This change is batch-pull only.
- Cross-region source distribution to dodge Cloud Run IP reputation — out of scope.

## Decisions

### Decision 1: `asyncio.gather` with bounded semaphore (size 5)

**Choice**: refactor `pipeline/main.py:run_pipeline()` so each `process_source(sid, sem)` call awaits its own crawl+extract+process pipeline; wrap with `asyncio.gather(..., return_exceptions=True)`. A `Semaphore(5)` caps concurrency.

**Why**:
- A Cloud Run Job execution is a single process; `asyncio` is the lightest way to multiplex it.
- `return_exceptions=True` keeps one bad source from killing the others.
- 5 is a tunable default; can be moved to env var (`PIPELINE_CONCURRENCY`).

**Alternatives considered**:
- **Multiple Cloud Run Job executions in parallel** (e.g. `gcloud run jobs execute` × 5). Rejected: each execution incurs cold-start cost and they don't share a process-local throttle, so per-domain throttling becomes a distributed problem.
- **Celery fan-out** (Redis + workers). Rejected: Redis isn't deployed in prod yet, and adding it for this change is too much.

### Decision 2: Per-hostname in-process token bucket

**Choice**: a `HostnameThrottle` class in `pipeline/crawler.py` that maintains one `asyncio.Lock`-protected `last_request_at: dict[str, float]`. Before any HTTP request, `await th.wait_for_slot(hostname)` sleeps until `now - last[hostname] >= tier.min_interval`.

**Why**:
- Simpler than a sliding-window token bucket, sufficient for 1-10 sources per hostname.
- Process-local is OK: each Cloud Run Job execution is a single process.
- Honors `Retry-After` headers: if a 429/403 comes back, set `last[hostname] = now + retry_after_seconds`.

**Alternatives considered**:
- **Redis-backed distributed throttle** — overkill for one process.
- **Per-URL throttle** — too granular; the unit that gets banned is the domain.

### Decision 3: `sources.tier` column + crawl defaults table

**Choice**: add `tier` (smallint 1-3) and `min_request_interval_seconds` (numeric 4,2) to the `sources` table. Default values by tier: T1=0.5s, T2=2s, T3=5s. The crawler reads the tier and applies the throttle + extra stealth automatically.

**Why**:
- Putting the tier in the DB means a Tier 1 source can be promoted to Tier 2 (or demoted) without a code change.
- Storing `min_request_interval_seconds` per source overrides the tier default for outliers (e.g. a Tier 1 site that's been 403-ing).

**Alternatives considered**:
- **Hardcoded map in code** — fine for now, but future source onboarding gets a config file lookup anyway, so just put it in the DB.

### Decision 4: Geocoding dedup before API call

**Choice**: in `backend/api/services/event_processing.py:resolve_location()`, before creating a new `Location`, query `locations` for `LOWER(name) = LOWER(:n) AND deleted_at IS NULL` and reuse the match. Only when the query returns no rows do we create a new row and trigger geocoding.

**Why**:
- `LOWER(name) = LOWER(:n)` is a simple, correct dedup signal for the case where two events at the same venue arrive in sequence.
- Reuses the `normalize_location_name()` helper that already lives in `api/services/geocoding.py`.
- Zero new dependencies.

**Alternatives considered**:
- **Fuzzy match (pg_trgm / Levenshtein)** — more recall, more false positives, more compute. Out of scope; revisit when we have >1000 events.
- **Hash on canonical name** — same as above; no benefit at our scale.

### Decision 5: Staggered Cloud Scheduler jobs

**Choice**: create 2-3 Cloud Scheduler jobs that invoke `fomoccs-pipeline` with different `--ids` subsets and cadences:
- `ingest-ticketing` — every 6h — MakeTicket, Superboletos, Eventbrite
- `ingest-venues` — every 12h — CC Chacao, Celarg, Museo de Ciencias, Teatro TC, CCS Cultura en Movimiento
- `ingest-tier2` — every 24h — Goliiive, El Diario, Ticketshow

**Why**: ticketing platforms rotate events faster than venue calendars, so they need more frequent pulls without being aggressive about the site. Splitting them lets us tune cadence independently.

**Alternatives considered**:
- **One job, all sources, every 6h** — wastes Cloud Run minutes on slow-changing sources.
- **Trigger-on-publish (webhook)** — most sources don't have webhooks.

### Decision 6: Structured logs in pipeline for monitoring

**Choice**: every source completion emits a single JSON line with `source_id`, `crawl_status_code`, `event_count`, `llm_provider`, `geocode_provider`, `geocode_hit`, `duration_s`. Cloud Logging already ingers stdout; we just need the format.

**Why**:
- A Cloud Logging query (`jsonPayload.event_count=0 AND source_id=11`) trivially surfaces a degraded source.
- No metrics agent, no extra dependencies.
- Pairs naturally with the throttle (we can grep for 403/429 to know when to bump the tier).

## Risks / Trade-offs

- **Risk**: Parallelism + throttling could still trip Cloudflare or Akamai on a Tier 1 site. → **Mitigation**: start with `PIPELINE_CONCURRENCY=3` for the first week, then ramp to 5. The throttle is independent of concurrency (per-hostname), so 5 sources in parallel can still hit the same domain with proper spacing.

- **Risk**: `LOWER(name) = LOWER(:n)` dedup produces false positives when two distinct venues share a name (e.g. two "Teatro Municipal"s). → **Mitigation**: add `address` to the dedup query as a tiebreaker: `LOWER(name)=LOWER(:n) AND (LOWER(address)=LOWER(:a) OR address IS NULL)`. False positives become negligible.

- **Risk**: New sources at T1 may have inconsistent event formats, leading to LLM extraction failures. → **Mitigation**: add per-source extraction prompt override (`source_settings.prompt_override`) so we can tune the extraction per site without changing the global prompt.

- **Risk**: `sources.tier` migration adds latency to the existing pipeline if it runs during deploy. → **Mitigation**: add the column as nullable with a default of 1; backfill in a single UPDATE; tier assignment is a one-time manual edit per source.

- **Trade-off**: Per-process throttle means throttle state is lost on every job execution (Cloud Run kills the process). → **Acceptable**: each run starts fresh; the throttling only protects within a run.

## Migration Plan

1. **Phase 1** (this change, low risk): add columns (`tier`, `min_request_interval_seconds`), set defaults, refactor `main.py` to asyncio.gather, add throttling, add dedup. Deploy pipeline image. Smoke-test on 3 sources.
2. **Phase 2** (this change, medium risk): add the 8-12 new sources, stagger cadences with Cloud Scheduler, enable monitoring queries. Run for 1 week, watch for 403/429 spikes.
3. **Phase 3** (separate change, if needed): Tier 3 sources (Instagram), residential proxies.

**Rollback**: revert the pipeline image to the previous Cloud Run revision; the new sources and tier columns are inert without the new code. The dedup is best-effort and won't cause data loss.

## Open Questions

- Should we add a `prompt_override` per source now, or wait until extraction quality becomes a problem?
- For the scheduler jobs, do we want a `failed_runs` alert, and at what threshold (e.g. 3 consecutive 0-event runs)?
- Tier 1 sources: should the throttle be per-source or per-(source, hostname)? Some sources have multiple URLs on the same domain.
