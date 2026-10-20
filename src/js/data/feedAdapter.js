/**
 * FeedAdapter
 *
 * Translates this deployment's live API into the data shape the rest of the
 * frontend expects.
 *
 * fomo.nyc's UI reads static JSON exports produced by its pipeline. We read a
 * live FastAPI feed instead, because the events here come from Postgres and
 * there is no export step. That difference is not cosmetic — the frontend
 * keys events by `id`, groups venues by `place_id`, and derives a format from
 * `event_type`, none of which the feed used to return.
 *
 * This module is the single place that reconciles the two. Everything
 * downstream (DataManager, the map, the selectors) only ever sees the fomo
 * shape, so re-plumbing this app onto static exports later means rewriting
 * this file rather than the UI.
 *
 * What the adapter derives, and why:
 *
 *   id          already in the feed. Required: recurring events merge by id.
 *   place_id    already in the feed. Venue identity, ahead of name/coords.
 *   icon_id     no icon packs are bundled, so this stays null and the marker
 *               falls back to the event's emoji.
 *   event_type  derived from the event's tags via the format taxonomy, since
 *               the DB has no event_type column. Drives the format selector
 *               and the marker colour categories.
 *   keywords    tags with no entry in the curated hierarchy. Kept separate so
 *               the selectors stay browsable while search still finds them.
 *
 * @module FeedAdapter
 */
const FeedAdapter = (() => {
    // ---------------------------------------------------------------
    // Networking
    // ---------------------------------------------------------------

    /**
     * Describes a fetch() failure in terms the user can act on.
     *
     * A bare `Failed to fetch` is what the browser reports for several very
     * different problems — DNS failure, connection refused, and, most often
     * here, a CORS rejection. Chrome reports the latter even when the server
     * answered, because a blocked cross-origin response is indistinguishable
     * from no response at all. Telling the user to "check your internet
     * connection" sends them to the wrong place when the real fault is a 5xx
     * or a blocked origin, so name the likely cause and keep the raw message
     * for the console.
     *
     * @param {TypeError} fetchError - The rejected error from fetch()
     * @returns {string} A user-facing explanation
     */
    function describeFetchFailure(fetchError) {
        const raw = fetchError?.message || 'Failed to fetch';
        if (/failed to fetch|networkerror/i.test(raw)) {
            return 'Could not reach the server. It may be down, or this site may not be allowed to query it from the browser. Please try again shortly.';
        }
        return `Network error: ${raw}`;
    }

    /**
     * Fetches and parses one JSON endpoint.
     *
     * @param {string} url - Absolute or same-origin URL
     * @param {number} [timeout] - Timeout in milliseconds
     * @returns {Promise<any>} Parsed JSON
     * @throws {Error} Network, timeout, or HTTP errors with user-facing text
     */
    async function fetchJSON(url, timeout = 30000) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        let response;
        try {
            response = await fetch(url, { signal: controller.signal });
        } catch (fetchError) {
            throw new Error(describeFetchFailure(fetchError));
        } finally {
            clearTimeout(timeoutId);
        }

        if (!response.ok) {
            if (response.status >= 500) {
                // 503 specifically means a Cloud Run revision is cold or
                // failing its start-up probe; both resolve on retry.
                throw new Error(`The server is temporarily unavailable (HTTP ${response.status}). This is not a problem with your connection — please try again in a minute.`);
            }
            if (response.status === 404) {
                throw new Error('Data file not found (404). The requested resource may have been moved or deleted.');
            }
            if (response.status >= 400) {
                throw new Error(`Client error (${response.status}). Please refresh the page and try again.`);
            }
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return response.json();
    }

    // ---------------------------------------------------------------
    // Normalization
    // ---------------------------------------------------------------

    /**
     * Derives an event's format from its tags.
     *
     * The format taxonomy maps each format to the tags that imply it, so an
     * event tagged `Concierto` + `Jazz` is a Concierto whose topic is Música.
     * Returns the format covering the most tags, so a tag list like
     * `['Festival', 'FestivalDeMusica']` does not depend on tag order.
     *
     * @param {string[]} tags - The event's raw tags
     * @param {Object} formatTags - format name -> implied tag names
     * @param {Object} tagRedirects - raw tag -> canonical tag
     * @returns {string} A format name, or 'Other' when nothing matches
     */
    function deriveEventType(tags, formatTags, tagRedirects) {
        if (!tags || !formatTags) return 'Other';
        const canonical = new Set(tags.map(t => (tagRedirects && tagRedirects[t]) || t));

        let best = 'Other';
        let bestHits = 0;
        for (const [format, implied] of Object.entries(formatTags)) {
            if (!Array.isArray(implied)) continue;
            const hits = implied.filter(tag => canonical.has(tag)).length;
            if (hits > bestHits) {
                best = format;
                bestHits = hits;
            }
        }
        return best;
    }

    /**
     * Normalizes one API event record.
     *
     * @param {Object} raw - Event record from /api/v1/feed/events
     * @param {Object} ctx - { formats, tagRedirects, curatedTags }
     * @returns {Object|null} Normalized event, or null when unusable
     */
    function normalizeEvent(raw, ctx) {
        // The map cannot place an event without coordinates, and a marker with
        // no link anywhere is a dead end for the user. Drop them here so every
        // downstream stage can assume both.
        if (raw.lat == null || raw.lng == null) return null;
        if (!raw.urls || raw.urls.length === 0) return null;

        const rawTags = Array.isArray(raw.tags) ? raw.tags : [];
        const tags = [];
        const keywords = [];
        for (const tag of rawTags) {
            const canonical = ctx.tagRedirects?.[tag] || tag;
            if (ctx.curatedTags.has(canonical)) {
                if (!tags.includes(canonical)) tags.push(canonical);
            } else if (!keywords.includes(tag)) {
                // Uncurated tags are kept verbatim, not canonicalized: they
                // are what the user can search for.
                keywords.push(tag);
            }
        }

        return {
            id: raw.id,
            event_type: deriveEventType(rawTags, ctx.formatTags, ctx.tagRedirects),
            place_id: raw.place_id ?? null,
            name: raw.name,
            location: raw.location || null,
            emoji: raw.emoji || null,
            tags,
            keywords,
            lat: Number(raw.lat),
            lng: Number(raw.lng),
            occurrences: Array.isArray(raw.occurrences) ? raw.occurrences : [],
            urls: raw.urls,
            description: raw.description || '',
            short_name: raw.short_name || null,
            sublocation: raw.sublocation || null,
            // No icon packs are bundled in this deployment; IconManager falls
            // back to the emoji when this is null.
            icon_id: null,
        };
    }

    /**
     * Normalizes one API location record.
     *
     * @param {Object} raw - Location record from /api/v1/feed/locations
     * @returns {Object} Normalized location
     */
    function normalizeLocation(raw) {
        return {
            id: raw.id ?? null,
            name: raw.name,
            short_name: raw.short_name || null,
            very_short_name: raw.very_short_name || null,
            lat: Number(raw.lat),
            lng: Number(raw.lng),
            emoji: raw.emoji || null,
            address: raw.address || null,
            description: raw.description || null,
            website_url: raw.website_url || null,
            tags: Array.isArray(raw.tags) ? raw.tags : [],
            icon_id: null,
        };
    }

    // ---------------------------------------------------------------
    // Public API
    // ---------------------------------------------------------------

    /**
     * Loads events and locations from the live feed.
     *
     * Both endpoints are fetched in parallel and normalized against the tag
     * hierarchy already loaded by App.
     *
     * @param {Object} options - { eventsUrl, locationsUrl, formatTags, tagRedirects, curatedTags }
     * @returns {Promise<{events: Object[], locations: Object[]}>}
     */
    async function load({ eventsUrl, locationsUrl, formatTags, tagRedirects, curatedTags }) {
        const ctx = { formatTags, tagRedirects, curatedTags: curatedTags || new Set() };

        const [rawEvents, rawLocations] = await Promise.all([
            fetchJSON(eventsUrl),
            fetchJSON(locationsUrl),
        ]);

        if (!Array.isArray(rawEvents)) {
            throw new Error('The server returned an unexpected response for events.');
        }
        if (!Array.isArray(rawLocations)) {
            throw new Error('The server returned an unexpected response for locations.');
        }

        const events = [];
        const seen = new Set();
        for (const raw of rawEvents) {
            const event = normalizeEvent(raw, ctx);
            // One record per event, but guard anyway: a duplicated id would
            // double its occurrences and inflate the tag counts.
            if (!event || seen.has(event.id)) continue;
            seen.add(event.id);
            events.push(event);
        }

        const locations = rawLocations
            .filter(loc => loc && loc.lat != null && loc.lng != null && loc.name)
            .map(normalizeLocation);

        return { events, locations };
    }

    return {
        load,
        deriveEventType,
        normalizeEvent,
        normalizeLocation,
        fetchJSON,
        describeFetchFailure,
    };
})();