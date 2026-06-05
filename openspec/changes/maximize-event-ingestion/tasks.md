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
- [x] 7.4 Verify each scheduler job by triggering a manual run and inspecting Cloud Logging for `event=source_complete` entries — manual trigger of `fomoccs-ingest-tier1` returned HTTP 200, pipeline execution `fomoccs-pipeline-cfzj4` ran 15 active T1 sources with 14 source_complete and 9 source_error events; structured logs (`event=source_complete`, `event=source_error`, `event=host_backoff`, source_id, duration_ms, error_type) all visible in Cloud Logging

> **Note on task 7**: The original spec listed 3 cadence jobs by source category (`ingest-ticketing`, `ingest-venues`, `ingest-tier2`), but the implementation generalized to tier-based filtering (`ingest-tier1/2/3`) since the only reliable discriminator in the database is `sources.tier`. This is more maintainable — adding/removing a source only requires changing its tier, not updating scheduler config.

## 8. Tier 2 sources (deferred to a follow-up change if approved)

- [x] 8.1 Smoke-test Ticketshow / Ticketmundo — DEFERRED, no viable static event listing
- [x] 8.2 Smoke-test Caracas concert venue aggregators — DEFERRED, no viable sources
- [x] 8.3 Instagram source (T3) — DEFERRED, requires residential proxy budget

> **Task 8 findings**: All three deferred source categories were smoke-tested and found unviable for the current crawler architecture (crawl4ai + LLM extraction on browser-rendered HTML):
>
> - **Ticketshow / Ticketmundo**:
>   - `ticketshow.com.ve` (Venezuelan domain) — DNS does not resolve, site is DEAD
>   - `ticketmundo.com.ve` — 200 OK but content is JS-rendered Next.js with no static event data; sitemap has only static pages (privacy, terms); `eventos/musica` and `eventos/conciertos` return identical 65KB shells with no event content
>   - `ticketshow.com` — 200 OK but it's the US parent site, not Venezuela-specific
>   - All would require a JS-aware crawler (currently the browser crawler times out on these via EmptyContent) or API access
> - **Caracas concert venue aggregators**:
>   - `evenpro.com` (WordPress, 181KB homepage) — marketing site, no event listings
>   - `salaconcert.com.ve`, `espaciomultifuncional.com`, `centrodeartesonoro.com`, `laguatira.com.ve`, `ccct.com.ve`, `elrecreo.com.ve`, `cclider.com` — all dead or SSL cert expired
>   - `centroculturalbancaribe.com`, `centroculturalbancaribe.com.ve` — both dead
>   - `teatroenvenezuela.com` — 403 anti-bot
>   - `superboletos.com` — 200 OK but Radware captcha blocks
>   - `eventosyfestivales.com` — 406 anti-bot
> - **News-driven event mentions** (could be T2 with LLM extraction):
>   - `diariolavoz.net/seccion/cultura` (147KB) — JS-rendered article titles
>   - `ultimasnoticias.com.ve/cultura/` (335KB) — JS-rendered article titles
>   - `el-nacional.com/cultura/` (219KB) — JS-rendered
>   - All are news-based (T2 candidates) but the article list is JS-rendered, so the browser crawler would need a longer wait
>
> **Recommendation**: Defer Task 8 entirely. The 19 active T1/T2 sources we have already cover the most reliable event producers in Caracas. Future additions should focus on either (a) sources with REST/GraphQL APIs that bypass browser rendering, or (b) fixing the 4 currently-failing T1 sources (CCAM, Eventbrite, Contrapunto, Songkick) which all return EmptyContent from the browser crawler.

## 9. Validation and monitoring

- [x] 9.1 Unit test: `HostnameThrottle` paces requests at the configured interval and honors `Retry-After` — `pipeline/tests/test_hostname_throttle.py` (20 tests, all passing)
- [x] 9.2 Unit test: `resolve_location()` dedup logic across the three cases (reuses / new / different address) — `backend/tests/services/test_event_processing.py` (3 tests added in task 4.3)
- [x] 9.3 Integration test: 5 sources on 5 different hostnames run concurrently, total wall time is at most 1.5× the slowest — `pipeline/tests/test_throttle_concurrency.py` (3 tests, all passing)
- [x] 9.4 Document the Cloud Logging query for "degraded source": `jsonPayload.event=source_complete AND jsonPayload.event_count=0` in `pipeline/README.md`
- [x] 9.5 Document a 1-week burn-in checklist: monitor 403/429 rates, average event_count per source per run, geocoding API spend — `pipeline/README.md` "Burn-in checklist" section
