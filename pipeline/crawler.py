"""
Web crawling module for the event processing pipeline.

Uses Crawl4AI to crawl event sources and store content in the database.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import db
import httpx
from crawl4ai import AsyncWebCrawler, CacheMode
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor

# Default timeout for crawl operations (in seconds)
DEFAULT_CRAWL_TIMEOUT = 180

# Minimum content size (in bytes) to consider a crawl successful.
# Crawls with less content than this are likely failed (e.g., JS-rendered
# pages that didn't load properly) and should be marked as failed.
MIN_CRAWL_CONTENT_SIZE = 500

# Default fallback cooldown when we get a 429 with no Retry-After header.
DEFAULT_BACKOFF_SECONDS = 30.0

# Default per-tier min intervals (seconds between requests to the same hostname).
# Sources can override via sources.min_request_interval_seconds.
DEFAULT_TIER_INTERVALS: dict[int, float] = {1: 0.5, 2: 2.0, 3: 5.0}

try:
    from crawl4ai import BrowserConfig, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
    from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
except ImportError:
    print("Error: crawl4ai is required.")
    print("Install it with: pip install crawl4ai")
    raise


def log_event(event: str, **fields: Any) -> None:
    """Emit a structured JSON log line to stdout.

    Cloud Run / Cloud Logging parses these as ``jsonPayload`` entries, so each
    field becomes a queryable log field. Use ``event=...`` as the log type
    discriminator (e.g., ``source_complete``, ``source_error``, ``host_backoff``).
    """
    payload = {"event": event, **fields}
    print(json.dumps(payload, default=str), file=sys.stdout, flush=True)


def hostname_of(url: str) -> str:
    """Extract the lowercase hostname (no port) from a URL."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


class HostnameThrottle:
    """Per-hostname request throttle with cooldown support for 429 responses.

    The throttle enforces two guarantees for each hostname:

    1. The interval between any two ``wait_for_slot`` calls is at least
       ``resolve_interval(source)`` seconds (the per-source override or
       the tier default).
    2. After ``backoff(hostname)`` is called (e.g., on a 429), the hostname
       is blocked for ``retry_after`` seconds (or ``DEFAULT_BACKOFF_SECONDS``).

    The throttle is async-safe: concurrent workers coordinate via ``asyncio``
    scheduling, not locks, so the cost of a ``wait_for_slot`` call when the
    slot is already free is a single dict lookup.
    """

    def __init__(
        self,
        tier_intervals: dict[int, float] | None = None,
        clock: "callable[[], float] | None" = None,
    ) -> None:
        self._tier_intervals = dict(tier_intervals or DEFAULT_TIER_INTERVALS)
        self._last_request: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._clock = clock or asyncio.get_event_loop().time

    def resolve_interval(self, source: dict[str, Any]) -> float:
        """Return the min interval (seconds) for a source.

        Per-source ``min_request_interval_seconds`` overrides the tier default.
        """
        override = source.get("min_request_interval_seconds")
        if override is not None:
            try:
                v = float(override)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                return v
        try:
            tier = int(source.get("tier") or 1)
        except (TypeError, ValueError):
            tier = 1
        return self._tier_intervals.get(tier, self._tier_intervals[1])

    async def wait_for_slot(self, hostname: str, interval: float) -> None:
        """Sleep until we can make a request to ``hostname``.

        Respects both the per-hostname cooldown (set by ``backoff``) and the
        minimum interval since the last request. Records the request time
        after the wait so the next caller measures from this point.
        """
        if not hostname:
            return
        now = self._clock()
        cooldown_end = self._cooldown_until.get(hostname, 0.0)
        cooldown_sleep = max(0.0, cooldown_end - now)
        last = self._last_request.get(hostname, 0.0)
        interval_sleep = max(0.0, last + interval - now)
        sleep_for = max(cooldown_sleep, interval_sleep)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        self._last_request[hostname] = self._clock()

    def backoff(
        self,
        hostname: str,
        retry_after: float | None = None,
        reason: str = "429",
    ) -> float:
        """Mark ``hostname`` as backed off. Returns the cooldown duration in seconds.

        ``retry_after`` should be the value from the ``Retry-After`` header if
        present, else ``None`` to fall back to ``DEFAULT_BACKOFF_SECONDS``.
        """
        if not hostname:
            return 0.0
        duration = DEFAULT_BACKOFF_SECONDS
        if retry_after is not None:
            try:
                duration = max(float(retry_after), DEFAULT_BACKOFF_SECONDS)
            except (TypeError, ValueError):
                pass
        self._cooldown_until[hostname] = self._clock() + duration
        log_event(
            "host_backoff",
            hostname=hostname,
            reason=reason,
            cooldown_seconds=duration,
        )
        return duration

    def stats(self) -> dict[str, Any]:
        """Snapshot of throttle state (for tests / debug)."""
        return {
            "hostnames_tracked": len(self._last_request),
            "cooldowns_active": len(
                [h for h, t in self._cooldown_until.items() if t > self._clock()]
            ),
        }


def strip_jsonp(text: str, callback_name: str | None = None) -> str:
    """Strip JSONP callback wrapper to get pure JSON string.

    If callback_name provided, strip that exact prefix.
    Otherwise use generic regex for any callback function name.
    Returns text unchanged if neither matches (already plain JSON).
    """
    text = text.strip()
    if callback_name:
        prefix = callback_name + "("
        if text.startswith(prefix):
            text = text[len(prefix) :]
            # Remove trailing );
            text = text.rstrip(";").rstrip()
            if text.endswith(")"):
                text = text[:-1]
            return text
    # Generic JSONP pattern
    match = re.match(r"^[a-zA-Z_]\w*\s*\((.*)\)\s*;?\s*$", text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def filter_by_date_window(
    events_dict: dict[str, Any], days_ahead: int = 30
) -> dict[str, Any]:
    """Filter events dict (keyed by event ID) to only those with upcoming dates.

    Keeps events that have at least one proxima_fecha within the date window,
    or events with NO proxima_fecha at all (let Gemini decide relevance).
    Only excludes events whose ALL dates are past or beyond the window.
    """
    now = datetime.now()
    window_end = now + timedelta(days=days_ahead)
    filtered = {}

    for event_id, event in events_dict.items():
        dates_found = []

        # Navigate: event > lugares > * > funciones > * > proxima_fecha
        lugares = event.get("lugares", {})
        if isinstance(lugares, dict):
            for lugar_id, lugar in lugares.items():
                funciones = lugar.get("funciones", {})
                if isinstance(funciones, dict):
                    for func_id, funcion in funciones.items():
                        proxima = funcion.get("proxima_fecha")
                        if proxima:
                            try:
                                dt = datetime.strptime(proxima, "%Y-%m-%d %H:%M")
                                dates_found.append(dt)
                            except (TypeError, ValueError):
                                pass

        if not dates_found:
            # No dates found -- include (let Gemini decide)
            filtered[event_id] = event
        elif any(now <= dt <= window_end for dt in dates_found):
            # At least one date within window
            filtered[event_id] = event
        # else: all dates are past or beyond window -- exclude

    return filtered


CLASIFICACION_EMOJI_MAP = {
    "teatro": "\U0001f3ad",
    "danza": "\U0001f483",
    "música": "\U0001f3b5",
    "musica": "\U0001f3b5",
    "humor": "\U0001f923",
    "circo": "\U0001f3aa",
    "infantil": "\U0001f9f8",
    "títeres": "\U0001f3ad",
    "titeres": "\U0001f3ad",
    "stand up": "\U0001f3a4",
    "comedia musical": "\U0001f3b6",
    "unipersonal": "\U0001f3ad",
    "monólogo": "\U0001f3a4",
    "monologo": "\U0001f3a4",
    "improvisación": "\U0001f3ad",
    "improvisacion": "\U0001f3ad",
    "ópera": "\U0001f3b6",
    "opera": "\U0001f3b6",
    "clown": "\U0001f921",
    "varieté": "\U0001f3aa",
    "variete": "\U0001f3aa",
    "performance": "\U0001f3ad",
    "biodrama": "\U0001f3ac",
    "poesía": "\U0001f4d6",
    "poesia": "\U0001f4d6",
}


def _pick_emoji(clasificaciones_dict: dict[str, Any]) -> str:
    """Pick a single emoji from clasificaciones, falling back to calendar."""
    if not isinstance(clasificaciones_dict, dict):
        return "\U0001f4c5"  # default calendar
    for _cid, clas in clasificaciones_dict.items():
        desc = (clas.get("descripcion") or "").lower().strip()
        if desc in CLASIFICACION_EMOJI_MAP:
            return CLASIFICACION_EMOJI_MAP[desc]
    return "\U0001f3ad"  # generic performing arts


def map_json_api_to_extracted(
    events_dict: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Map Alternativa Teatral structured JSON directly to the Event schema.

    Returns a dict ``{"events": [...]}``, the format that
    ``_parse_json_events()`` in processor.py expects.  This bypasses Gemini
    extraction entirely for structured API sources.

    The mapping is intentionally hardcoded for Alternativa Teatral.
    """
    mapped_events = []

    for event_id, event in events_dict.items():
        titulo = event.get("titulo", f"Event {event_id}")
        url_slug = event.get("url", "")
        url = f"https://www.alternativateatral.com/{url_slug}" if url_slug else None

        # Clasificaciones -> hashtags + emoji + description parts
        clasificaciones = event.get("clasificaciones", {})
        hashtags = []
        desc_parts = []
        if isinstance(clasificaciones, dict):
            for _cid, clas in clasificaciones.items():
                desc = clas.get("descripcion", "")
                if desc:
                    hashtags.append(desc)
                    desc_parts.append(desc)

        emoji = _pick_emoji(clasificaciones)

        # Lugares -> location + occurrences
        lugares = event.get("lugares", {})
        if isinstance(lugares, dict):
            for _lid, lugar in lugares.items():
                nombre = lugar.get("nombre", "")
                if not nombre:
                    continue

                # Build description from clasificaciones + venue
                if desc_parts:
                    description = f"{', '.join(desc_parts)} en {nombre}."
                else:
                    description = f"Evento en {nombre}."

                # Collect occurrences from funciones
                occurrences = []
                funciones = lugar.get("funciones", {})
                if isinstance(funciones, dict):
                    for _fid, funcion in funciones.items():
                        proxima = funcion.get("proxima_fecha", "")
                        hora = funcion.get("hora", "")

                        # proxima_fecha is "YYYY-MM-DD HH:MM"
                        start_date = ""
                        start_time = ""
                        if proxima:
                            parts = proxima.split(" ", 1)
                            start_date = parts[0]
                            if len(parts) > 1:
                                # Convert 24h to 12h for consistency
                                try:
                                    dt = datetime.strptime(parts[1], "%H:%M")
                                    start_time = dt.strftime("%I:%M %p").lstrip("0")
                                except (ValueError, TypeError):
                                    start_time = parts[1]
                        elif hora:
                            # No proxima_fecha but has hora — skip, no date
                            continue

                        if start_date:
                            occ = {
                                "start_date": start_date,
                                "start_time": start_time or None,
                                "end_date": None,
                                "end_time": None,
                            }
                            occurrences.append(occ)

                if not occurrences:
                    continue

                mapped_events.append(
                    {
                        "name": titulo,
                        "location": nombre,
                        "sublocation": None,
                        "occurrences": occurrences,
                        "description": description,
                        "url": url,
                        "hashtags": hashtags if hashtags else ["Teatro"],
                        "emoji": emoji,
                    }
                )

    return {"events": mapped_events}


def flatten_events_to_markdown(events_dict: dict[str, Any]) -> str:
    """Convert filtered JSON events dict to markdown for Gemini extraction.

    For each event produces:
    - Title, tags, venues with addresses, show times, URL, tickets link
    - Blank line between events
    """
    lines = []

    for event_id, event in events_dict.items():
        titulo = event.get("titulo", f"Event {event_id}")
        lines.append(f"## {titulo}")

        # Tags from clasificaciones
        clasificaciones = event.get("clasificaciones", {})
        if isinstance(clasificaciones, dict):
            tag_names = [
                c.get("descripcion", "")
                for c in clasificaciones.values()
                if c.get("descripcion")
            ]
            if tag_names:
                lines.append(f"**Tags:** {', '.join(tag_names)}")

        # Venues and showtimes
        lugares = event.get("lugares", {})
        if isinstance(lugares, dict):
            for lugar_id, lugar in lugares.items():
                nombre = lugar.get("nombre", "")
                direccion = lugar.get("direccion", "")
                zona = lugar.get("zona", "")
                if nombre:
                    lines.append(f"**Venue:** {nombre}")
                addr_parts = [p for p in [direccion, zona] if p]
                if addr_parts:
                    lines.append(f"**Address:** {', '.join(addr_parts)}")

                funciones = lugar.get("funciones", {})
                if isinstance(funciones, dict):
                    for func_id, funcion in funciones.items():
                        dia = funcion.get("dia", "")
                        hora = funcion.get("hora", "")
                        proxima = funcion.get("proxima_fecha", "")
                        parts = [p for p in [dia, hora] if p]
                        time_str = " ".join(parts)
                        if proxima:
                            time_str += f" (next: {proxima})"
                        if time_str.strip():
                            lines.append(f"- {time_str.strip()}")

        # URL
        url_slug = event.get("url", "")
        if url_slug:
            lines.append(f"**URL:** https://www.alternativateatral.com/{url_slug}")

        # Tickets
        url_entradas = event.get("url_entradas", "")
        if url_entradas:
            lines.append(f"**Tickets:** {url_entradas}")

        lines.append("")  # Blank line between events

    return "\n".join(lines)


async def crawl_json_api(
    source: dict[str, Any],
    cursor: PgCursor,
    connection: PgConnection,
    crawl_job_id: int,
    throttle: HostnameThrottle | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Crawl a source via HTTP GET to a JSON/JSONP API endpoint.

    Args:
        source: Source dict with json_api_config, name, id, etc.
        cursor: Database cursor
        connection: Database connection
        crawl_job_id: ID of the current crawl job
        throttle: Optional per-hostname throttle to pace requests. ``None``
            disables throttling (useful for tests).

    Returns:
        crawl_result_id if successful, None otherwise
    """
    name = source["name"]
    config = source.get("json_api_config", {})
    source_id = source.get("id")
    started_at = asyncio.get_event_loop().time()

    if not config:
        print(f"  Skipping {name}: no json_api_config")
        return None, None

    # Create crawl result record
    crawl_result_id = db.create_crawl_result(
        cursor, connection, crawl_job_id, source["id"]
    )

    try:
        # Determine URL
        url = config.get("base_url")
        if not url:
            urls = source.get("urls", [])
            if urls:
                url = urls[0]["url"] if isinstance(urls[0], dict) else urls[0]
        if not url:
            db.update_crawl_result_failed(
                cursor, connection, crawl_result_id, "No URL configured"
            )
            db.update_source_last_crawled(cursor, connection, source["id"])
            log_event(
                "source_error",
                source_id=source_id,
                source_name=name,
                crawl_result_id=crawl_result_id,
                error="No URL configured",
                duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
            )
            return None, None

        print(f"  Crawling {name} via JSON API...")
        print(f"    - GET {url}")

        # Per-hostname throttle: enforce min interval between requests.
        hostname = hostname_of(url)
        if throttle and hostname:
            await throttle.wait_for_slot(hostname, throttle.resolve_interval(source))

        # HTTP GET
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
        except httpx.HTTPStatusError as e:
            if throttle and hostname and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                try:
                    retry_after_f = float(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    retry_after_f = None
                throttle.backoff(hostname, retry_after_f, reason="429")
            raise
        if response.status_code == 429 and throttle and hostname:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_f = float(retry_after) if retry_after else None
            except (TypeError, ValueError):
                retry_after_f = None
            throttle.backoff(hostname, retry_after_f, reason="429")
        response.raise_for_status()

        raw_text = response.text

        # Try plain JSON first, fall back to JSONP stripping.
        # Strip BOM after each step — it can appear at the start of
        # the response OR inside a JSONP wrapper (e.g. `callback(\ufeff{…})`).
        raw_text = raw_text.lstrip("\ufeff")
        jsonp_callback = config.get("jsonp_callback")
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            raw_text = strip_jsonp(raw_text, jsonp_callback)
            raw_text = raw_text.lstrip("\ufeff")
            data = json.loads(raw_text)

        # Navigate data_path (e.g., 'espectaculos')
        data_path = config.get("data_path", "")
        if data_path:
            for key in data_path.split("."):
                if key and isinstance(data, dict):
                    data = data.get(key, {})

        # Capture pre-filter data for location resolution
        pre_filter_data = data

        total_events = len(data) if isinstance(data, dict) else 0
        print(f"    - Total events in API: {total_events}")

        # Filter by date window
        date_window_days = config.get("date_window_days", 30)
        if isinstance(data, dict):
            filtered = filter_by_date_window(data, date_window_days)
        else:
            filtered = data

        filtered_count = len(filtered) if isinstance(filtered, dict) else 0
        print(f"    - Events after date filter ({date_window_days}d): {filtered_count}")

        # Flatten to markdown (kept for audit/debugging)
        markdown = flatten_events_to_markdown(filtered)

        if not markdown.strip():
            db.update_crawl_result_failed(
                cursor, connection, crawl_result_id, "No events after filtering"
            )
            db.update_source_last_crawled(cursor, connection, source["id"])
            log_event(
                "source_error",
                source_id=source_id,
                source_name=name,
                crawl_result_id=crawl_result_id,
                mode="json_api",
                error="No events after filtering",
                error_type="EmptyContent",
                duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
            )
            return None, None

        # Store markdown as crawled_content for audit trail
        db.update_crawl_result_crawled(cursor, connection, crawl_result_id, markdown)

        # Map structured JSON directly to extracted Event schema,
        # skipping Gemini extraction entirely for this structured source
        extracted_data = map_json_api_to_extracted(filtered)
        extracted_event_count = len(extracted_data.get("events", []))

        if extracted_event_count > 0:
            db.update_crawl_result_extracted(
                cursor, connection, crawl_result_id, json.dumps(extracted_data)
            )
            print(
                f"    - Directly mapped {extracted_event_count} events "
                f"from {filtered_count} source events "
                f"(skipped Gemini extraction)"
            )
        else:
            print(
                f"    - Stored {len(markdown)} chars of markdown "
                f"({filtered_count} events, no direct mapping)"
            )

        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_complete",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="json_api",
            events_mapped=extracted_event_count,
            events_filtered=filtered_count,
            content_bytes=len(markdown),
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return crawl_result_id, pre_filter_data

    except Exception as e:
        error_msg = str(e)
        print(f"    - Error crawling {name}: {error_msg}")
        db.update_crawl_result_failed(cursor, connection, crawl_result_id, error_msg)
        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="json_api",
            error=error_msg,
            error_type=type(e).__name__,
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return None, None


def _build_crawl_config_kwargs(source: dict[str, Any]) -> dict[str, Any]:
    """Build CrawlerRunConfig kwargs from source settings.

    Returns a dict of kwargs ready for ``CrawlerRunConfig(**kwargs)``.
    Callers can override individual keys (e.g. js_code) before constructing.
    """
    # JavaScript code for dynamic content loading
    js_code = source.get("js_code") or ""
    if not js_code:
        selector = source.get("selector")
        num_clicks = source.get("num_clicks", 2)
        if selector and num_clicks:
            js_code = (
                f"for (let i = 0; i < {num_clicks}; i++) {{"
                f"await new Promise(resolve => setTimeout(resolve, 1000)); "
                f"document.querySelector('{selector}').click();}}"
            )

    # Deep crawling strategy based on keywords
    keywords = source.get("keywords")
    if keywords:
        filters = [f"*{k.strip()}*" for k in keywords.split(", ")]
        url_filter = URLPatternFilter(patterns=filters)
        deep_crawl_strategy = BestFirstCrawlingStrategy(
            max_depth=1,
            include_external=True,
            filter_chain=FilterChain([url_filter]),
            max_pages=source.get("max_pages", 30),
        )
    else:
        deep_crawl_strategy = None

    # Markdown generator with optional content filter
    filter_threshold = source.get("content_filter_threshold")
    if filter_threshold is not None and float(filter_threshold) > 0:
        md_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=float(filter_threshold),
                threshold_type="fixed",
                min_word_threshold=0,
            ),
            options={"ignore_links": False},
        )
    else:
        md_generator = DefaultMarkdownGenerator(
            options={"ignore_links": False},
        )

    scan_full_page = source.get("scan_full_page")
    if scan_full_page is None:
        scan_full_page = True

    return {
        "word_count_threshold": 5,
        "excluded_tags": [],
        "process_iframes": True,
        "cache_mode": CacheMode.BYPASS,
        "js_code": js_code,
        "remove_overlay_elements": source.get("remove_overlay_elements", False),
        "delay_before_return_html": source.get("delay_before_return_html") or 5,
        "scan_full_page": scan_full_page,
        "scroll_delay": source.get("scroll_delay") or 0.2,
        "page_timeout": 60000,
        "wait_until": "domcontentloaded",
        "ignore_body_visibility": True,
        "deep_crawl_strategy": deep_crawl_strategy,
        "markdown_generator": md_generator,
    }


async def _crawl_all_urls(
    crawler: AsyncWebCrawler,
    urls: list[dict[str, Any] | str],
    config_kwargs: dict[str, Any],
    crawler_config: CrawlerRunConfig,
    cursor: PgCursor,
    connection: PgConnection,
    crawl_result_id: int,
    throttle: HostnameThrottle | None = None,
    source: dict[str, Any] | None = None,
) -> str:
    """Crawl all URLs for a source, persisting each result to the database.

    Each URL gets its own ``crawl_url_results`` row so that partial progress
    survives timeouts and individual failures are tracked independently.

    ``throttle``, when provided, enforces a per-hostname min interval between
    ``arun`` calls. ``source`` is needed to resolve the tier/override interval.

    Returns the combined markdown from all successful URLs.
    """
    combined_parts: list[str] = []
    interval = throttle.resolve_interval(source) if (throttle and source) else 0.0

    for url_data in urls:
        # Handle both dict format (with js_code) and string format (legacy)
        if isinstance(url_data, dict):
            url = resolve_url_templates(url_data["url"])
            url_js_code = url_data.get("js_code")
        else:
            url = resolve_url_templates(url_data)
            url_js_code = None

        # Create per-URL tracking record
        url_result_id = db.create_crawl_url_result(
            cursor, connection, crawl_result_id, url
        )

        # Use per-URL js_code if set, otherwise use source-level config
        if url_js_code:
            url_config = CrawlerRunConfig(**{**config_kwargs, "js_code": url_js_code})
        else:
            url_config = crawler_config

        print(f"    - Processing {url}")
        url_content = ""
        page_count = 0
        hostname = hostname_of(url)

        # Per-hostname throttle: enforce min interval between requests.
        if throttle and hostname:
            await throttle.wait_for_slot(hostname, interval)

        try:
            arun_result = await crawler.arun(url=url, config=url_config)
            print(
                f"    - arun returned: type={type(arun_result).__name__}, len={len(arun_result) if hasattr(arun_result, '__len__') else 'N/A'}"
            )
            for result in arun_result:
                page_count += 1
                html_len = len(result.html) if result and result.html else 0
                has_error = bool(result.error_message) if result else False
                print(
                    f"      Page {page_count}: html={html_len}, success={result.success if result else False}, error={result.error_message if has_error else 'none'}"
                )

                # Detect 429-style rate limiting in crawl4ai's error messages.
                if has_error and throttle and hostname:
                    msg = (result.error_message or "").lower()
                    if "429" in msg or "too many requests" in msg:
                        throttle.backoff(hostname, None, reason="429")

                if result and result.html and html_len > 1000:
                    raw_len = (
                        len(result.markdown.raw_markdown)
                        if result.markdown and result.markdown.raw_markdown
                        else 0
                    )
                    has_body = "<body" in result.html.lower()
                    if not has_body:
                        print(
                            f"      WARNING: HTML missing body tag (html={html_len}, raw_md={raw_len}) - possible crawl4ai bug"
                        )

                if result and result.markdown:
                    fit_len = (
                        len(result.markdown.fit_markdown)
                        if result.markdown.fit_markdown
                        else 0
                    )
                    raw_len = (
                        len(result.markdown.raw_markdown)
                        if result.markdown.raw_markdown
                        else 0
                    )
                    content = result.markdown.fit_markdown
                    if not content or len(content) < 500:
                        content = result.markdown.raw_markdown
                    if content:
                        url_content += content + "\n\n"
                    print(
                        f"      Page {page_count}: fit={fit_len}, raw={raw_len}, using={len(content) if content else 0}"
                    )

            print(f"    - Crawled {page_count} page(s), {len(url_content)} chars total")

            if url_content:
                db.update_crawl_url_result_crawled(
                    cursor, connection, url_result_id, url_content
                )
                combined_parts.append(url + "\n" + url_content)
            else:
                db.update_crawl_url_result_failed(
                    cursor, connection, url_result_id, "No content retrieved"
                )
        except Exception as e:
            error_msg = str(e)
            print(f"    - Error crawling URL {url}: {error_msg}")
            db.update_crawl_url_result_failed(
                cursor, connection, url_result_id, error_msg
            )
            # 429s sometimes bubble up as httpx / aiohttp errors.
            if throttle and hostname:
                msg = error_msg.lower()
                if "429" in msg or "too many requests" in msg:
                    throttle.backoff(hostname, None, reason="429")

    return "".join(combined_parts)


def resolve_url_templates(url: str) -> str:
    """Resolve date template placeholders in URLs.

    Supported placeholders:
        {{month}}           - current month name, lowercase (e.g. "february")
        {{year}}            - current year (e.g. "2026")
        {{next_month}}      - next month name, lowercase
        {{next_month_year}} - year of the next month (handles Dec->Jan rollover)
    """
    if "{{" not in url:
        return url
    now = datetime.now()
    next_month_date = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    replacements = {
        "{{month}}": now.strftime("%B").lower(),
        "{{year}}": str(now.year),
        "{{next_month}}": next_month_date.strftime("%B").lower(),
        "{{next_month_year}}": str(next_month_date.year),
    }
    for placeholder, value in replacements.items():
        url = url.replace(placeholder, value)
    return url


def _combine_successful_url_contents(cursor: PgCursor, crawl_result_id: int) -> str:
    """Build combined markdown from successfully crawled URL results."""
    rows = db.get_successful_url_contents(cursor, crawl_result_id)
    return "".join(url + "\n" + content for url, content in rows)


async def crawl_source(
    crawler: AsyncWebCrawler,
    source: dict[str, Any],
    cursor: PgCursor,
    connection: PgConnection,
    crawl_job_id: int,
    throttle: HostnameThrottle | None = None,
) -> int | None:
    """
    Crawl a source and store the content in the database.

    Args:
        crawler: AsyncWebCrawler instance
        source: Source dict with urls, name, selector, etc.
        cursor: Database cursor
        connection: Database connection
        crawl_job_id: ID of the current crawl job
        throttle: Optional per-hostname throttle (shared across workers).

    Returns:
        crawl_result_id if successful, None otherwise
    """
    name = source["name"]
    urls = source["urls"]
    source_id = source.get("id")
    started_at = asyncio.get_event_loop().time()

    if not urls:
        print(f"  Skipping {name}: no URLs configured")
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            error="No URLs configured",
            error_type="ConfigError",
            duration_ms=0,
        )
        return None

    # Create crawl result record
    crawl_result_id = db.create_crawl_result(
        cursor, connection, crawl_job_id, source["id"]
    )

    config_kwargs = _build_crawl_config_kwargs(source)
    crawler_config = CrawlerRunConfig(**config_kwargs)
    crawl_timeout = source.get("crawl_timeout") or DEFAULT_CRAWL_TIMEOUT

    print(f"  Crawling {name} (timeout: {crawl_timeout}s)...")

    # Execute crawl — the only operation that needs exception handling.
    # Individual URL results are persisted to crawl_url_results inside
    # _crawl_all_urls, so partial progress survives timeouts.
    try:
        combined_markdown = await asyncio.wait_for(
            _crawl_all_urls(
                crawler,
                urls,
                config_kwargs,
                crawler_config,
                cursor,
                connection,
                crawl_result_id,
                throttle=throttle,
                source=source,
            ),
            timeout=crawl_timeout,
        )
    except TimeoutError:
        error_msg = f"Crawl timed out after {crawl_timeout} seconds"
        print(f"    - {error_msg}")
        # Recover partial content from URLs that completed before timeout
        combined_markdown = _combine_successful_url_contents(cursor, crawl_result_id)
        if combined_markdown.strip():
            print(f"    - Saving partial content ({len(combined_markdown)} chars)")
            db.update_crawl_result_crawled(
                cursor, connection, crawl_result_id, combined_markdown
            )
            db.update_source_last_crawled(cursor, connection, source["id"])
            log_event(
                "source_complete",
                source_id=source_id,
                source_name=name,
                crawl_result_id=crawl_result_id,
                mode="browser",
                content_bytes=len(combined_markdown),
                timed_out=True,
                duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
            )
            return crawl_result_id
        db.update_crawl_result_failed(cursor, connection, crawl_result_id, error_msg)
        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="browser",
            error=error_msg,
            error_type="TimeoutError",
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return None
    except Exception as e:
        error_msg = str(e)
        print(f"    - Error crawling {name}: {error_msg}")
        db.update_crawl_result_failed(cursor, connection, crawl_result_id, error_msg)
        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="browser",
            error=error_msg,
            error_type=type(e).__name__,
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return None

    # Validate combined crawl results
    if not combined_markdown.strip():
        db.update_crawl_result_failed(
            cursor, connection, crawl_result_id, "No content retrieved"
        )
        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="browser",
            error="No content retrieved",
            error_type="EmptyContent",
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return None

    content_size = len(combined_markdown)
    if content_size < MIN_CRAWL_CONTENT_SIZE:
        error_msg = f"Crawled content too small ({content_size} bytes < {MIN_CRAWL_CONTENT_SIZE} minimum) - likely failed to load page content"
        print(f"    - {error_msg}")
        db.update_crawl_result_failed(cursor, connection, crawl_result_id, error_msg)
        db.update_source_last_crawled(cursor, connection, source["id"])
        log_event(
            "source_error",
            source_id=source_id,
            source_name=name,
            crawl_result_id=crawl_result_id,
            mode="browser",
            error=error_msg,
            error_type="ContentTooSmall",
            content_bytes=content_size,
            duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
        )
        return None

    # Store combined content for downstream extraction
    db.update_crawl_result_crawled(
        cursor, connection, crawl_result_id, combined_markdown
    )
    db.update_source_last_crawled(cursor, connection, source["id"])

    print(f"    - Stored {len(combined_markdown)} characters of content")
    log_event(
        "source_complete",
        source_id=source_id,
        source_name=name,
        crawl_result_id=crawl_result_id,
        mode="browser",
        urls_crawled=len(urls),
        content_bytes=content_size,
        duration_ms=int((asyncio.get_event_loop().time() - started_at) * 1000),
    )
    return crawl_result_id


def get_browser_config(
    javascript_enabled: bool = True,
    text_mode: bool = True,
    light_mode: bool = True,
    use_stealth: bool = False,
) -> BrowserConfig:
    """
    Get the browser configuration for crawling.

    Args:
        javascript_enabled: Whether to enable JavaScript execution (default: True).
                           Set to False for sites that freeze during JS execution.
        text_mode: If True, disables images for faster text-only crawls (default: True).
        light_mode: If True, uses minimal browser features for speed (default: True).
        use_stealth: If True, uses undetected browser mode to bypass bot detection (default: False).
                    Required for sites like Resident Advisor that have verification pages.

    Note: These are browser-level settings. All sources crawled with this
          config will share the same settings.
    """
    if use_stealth:
        # Use undetected browser mode with stealth features for bot detection bypass
        return BrowserConfig(
            headless=False,
            java_script_enabled=javascript_enabled,
            text_mode=text_mode,
            light_mode=light_mode,
            use_managed_browser=True,
            enable_stealth=True,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            extra_args=["--disable-blink-features=AutomationControlled"],
        )
    else:
        # Standard browser mode
        return BrowserConfig(
            headless=False,
            java_script_enabled=javascript_enabled,
            text_mode=text_mode,
            light_mode=light_mode,
        )
