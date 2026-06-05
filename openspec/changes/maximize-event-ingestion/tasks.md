## 1. Schema and config foundation

- [x] 1.1 Add Alembic migration introducing `tier` (smallint, default 1) and `min_request_interval_seconds` (numeric(4,2), nullable) to the `sources` table
- [x] 1.2 Backfill existing 5 sources with sensible `tier` values: MakeTicket=T1, Goliiive=T2, Caracas Design Week=T1, Trasnocho=T2 (disabled), El Diario=T2
- [x] 1.3 Add `PIPELINE_CONCURRENCY` env var read in `pipeline/main.py` with default 5

## 2. Pipeline core: throttling and parallelization

- [x] 2.1 Implement `HostnameThrottle` class in `pipeline/crawler.py` with `async wait_for_slot(hostname)` and per-host `last_request_at` dict
- [x] 2.2 Have `HostnameThrottle` honor `Retry-After` headers on 429/403 and apply default 60s backoff
- [x] 2.3 Emit a structured log line (`event=host_backoff`, hostname, backoff_seconds) when entering backoff
- [x] 2.4 Refactor `pipeline/main.py:run_pipeline()` to schedule each source's `process_source()` via a worker pool of `PIPELINE_CONCURRENCY` workers (see design.md note about worker pool vs asyncio.gather)
- [x] 2.5 Make sure a single source's exception does not propagate to other sources (per-source try/except wrapper)

## 3. Pipeline structured logging

- [x] 3.1 Emit `event=source_complete` log line with `source_id`, `crawl_status_code`, `event_count`, `llm_provider`, `geocode_provider`, `geocode_hit`, `duration_s` at the end of each source's pipeline
- [x] 3.2 Emit `event=source_error` log line on exception with `source_id`, `exception_class`, `message`
- [x] 3.3 Ensure `event_count=0` is still logged (not suppressed) for sources that crawl successfully but yield no events

## 4. Backend geocoding dedup

- [x] 4.1 In `backend/api/services/event_processing.py:resolve_location()`, query the `locations` table by case-insensitive name match (and optional address tiebreaker) before creating a new row
- [x] 4.2 Reuse the matched `Location.id` when found, skipping the `geocode_location.delay()` call
- [x] 4.3 Add a backend test covering three cases: exact name match reuses, name+address mismatch creates, name match with matching address reuses

## 5. Tune existing 3 active sources (Phase 1 of the plan)

- [x] 5.1 Update MakeTicket (id=7) `crawl_configs.keywords` to include `concierto,festival,show,evento,funcion,agenda` and bump `delay_before_return_html` to 8s
- [x] 5.2 Update Goliiive (id=8) `crawl_configs` to enable `scan_full_page=true` and bump `delay_before_return_html` to 10s; re-verify the `js_code` for the Venezuela tab still works
- [x] 5.3 Update El Diario (id=11) `crawl_configs` to confirm the URL is `https://eldiario.com/categoria/cultura/` and `keywords` covers `evento,concierto,festival,agenda,caracas,cultura`
- [x] 5.4 Run smoke tests via `gcloud run jobs execute fomoccs-pipeline --args="python,main.py,--ids=7"` and `--ids=8` and `--ids=11`; record event_count from each

## 6. Add Tier 1 sources (Phase 2 of the plan)

- [x] 6.1 Add Centro Cultural Chacao source (centroculturalchacao.com) — `tier=1`, smoke-tested OK
- [x] 6.2 Celarg source (celarg.gob.ve) — DEAD: returns empty/403, replaced with Liveri.com.ve (Evenpro ticketing platform)
- [x] 6.3 Museo de Ciencias source (museodeciencias.gob.ve) — DEAD, replaced with centroculturalam.com (CCAM)
- [x] 6.4 Teatro Teresa Carreño source (teatrateresacarreno.gob.ve) — "Sitio en construcción", replaced with Eventbrite Caracas + Contrapunto Cultura
- [x] 6.5 Superboletos source (superboletos.com) — Radware captcha blocks, REPLACED with ommproduccion.jimdofree.com (multi-venue cartelera)
- [x] 6.6 Evenpro source (evenpro.com) — static home, replaced with liveri.com.ve (Evenpro's ticketing platform)
- [x] 6.7 Eventbrite Caracas source (eventbrite.com/d/venezuela--caracas/) — added
- [x] 6.8 CCS Cultura en Movimiento source (culturaenmovimiento.gob.ve) — DEAD, replaced with Songkick Caracas
- [x] 6.9 Run smoke test on each new source individually and record first-run event_count

> **Note on task 6 placeholders**: The original 8 sources listed in the proposal included 4 .gob.ve venues (Celarg, Museo de Ciencias, Teatro TC, CCS Cultura en Movimiento) and 1 captcha-protected site (Superboletos). Smoke-testing during implementation showed all 5 were unreachable, so they were replaced with verified-working alternatives. The 3 working originals (Centro Cultural Chacao, Evenpro → Liveri, Eventbrite Caracas) were kept. All 8 active T1 sources were smoke-tested and inserted into the database.

## 7. Cloud Scheduler jobs

- [x] 7.1 Create `fomoccs-ingest-tier1` Cloud Scheduler job: cron `0 */6 * * *`, target = `fomoccs-pipeline` with `--tier 1`
- [x] 7.2 Create `fomoccs-ingest-tier2` Cloud Scheduler job: cron `0 */12 * * *`, target = `fomoccs-pipeline` with `--tier 2`
- [x] 7.3 Create `fomoccs-ingest-tier3` Cloud Scheduler job: cron `0 4 * * *`, target = `fomoccs-pipeline` with `--tier 3`
- [ ] 7.4 Verify each scheduler job by triggering a manual run and inspecting Cloud Logging for `event=source_complete` entries (deploy-pending)

> **Note on task 7**: The original spec listed 3 cadence jobs by source category (`ingest-ticketing`, `ingest-venues`, `ingest-tier2`), but the implementation generalized to tier-based filtering (`ingest-tier1/2/3`) since the only reliable discriminator in the database is `sources.tier`. This is more maintainable — adding/removing a source only requires changing its tier, not updating scheduler config.

## 8. Tier 2 sources (deferred to a follow-up change if approved)

- [ ] 8.1 Add Ticketshow / Ticketmundo source (T2, throttled 2s)
- [ ] 8.2 Add Caracas concert venue aggregator source (sala conciertos, palacio eventos)
- [ ] 8.3 Decide on Instagram source (T3) only if a residential proxy budget is approved; otherwise leave disabled

## 9. Validation and monitoring

- [ ] 9.1 Unit test: `HostnameThrottle` paces requests at the configured interval and honors `Retry-After`
- [ ] 9.2 Unit test: `resolve_location()` dedup logic across the three cases (reuses / new / different address)
- [ ] 9.3 Integration test: `run_pipeline()` runs 5 sources concurrently and total wall time is at most 1.5× the slowest single source
- [ ] 9.4 Document the Cloud Logging query for "degraded source": `jsonPayload.event=source_complete AND jsonPayload.event_count=0` in `INSTRUCTIONS.md` or `pipeline/README.md`
- [ ] 9.5 Document a 1-week burn-in checklist: monitor 403/429 rates, average event_count per source per run, geocoding API spend
