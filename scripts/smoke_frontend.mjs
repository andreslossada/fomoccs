/**
 * Smoke test for the built frontend.
 *
 * Serves a build over HTTP (the real deploy shape: a static bucket behind an
 * origin) and loads it in Chromium, then asserts the app came up: no console
 * errors, no failed same-origin requests, and the map, the selectors and the
 * event sheet actually rendered.
 *
 * The feed endpoints are stubbed with a small fixture so the test is
 * deterministic and needs neither the backend nor a database.
 *
 * Two bundles are checked:
 *   prod  dist/, the artefact that deploys. DOM-only assertions.
 *   dev   the same sources, unminified, built into a temp dir. The dev build
 *         exposes window.__fomo so the data-layer derivations can be asserted
 *         directly; production deliberately does not expose that handle.
 *
 *   npm test              # both
 *   node scripts/smoke_frontend.mjs --headed
 */
import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const PROD_DIST = join(ROOT, 'dist');
const HEADED = process.argv.includes('--headed');

const MIME = {
    '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
    '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
    '.pbf': 'application/x-protobuf',
};

const TODAY = new Date();
const iso = d => d.toISOString().slice(0, 10);
const inDays = n => {
    const d = new Date(TODAY);
    d.setDate(d.getDate() + n);
    return iso(d);
};

// Three events across two venues, so venue grouping, the recurring-event
// merge, the tag hierarchy and the format derivation all have something to
// chew on. Tags use the raw spellings the extractor actually produces.
const EVENTS = [
    {
        id: 101, name: 'C4 Trío', short_name: null,
        description: 'Concierto de cumbia.',
        emoji: '🎺', location: 'Centro Cultural Chacao', place_id: 7,
        sublocation: null, lat: 10.4946, lng: -66.9019,
        tags: ['Concierto', 'MusicaLatina', 'Salsa', 'EnVivo'],
        occurrences: [[inDays(2), '20:00:00', null, null], [inDays(3), '20:00:00', null, null]],
        urls: ['https://example.org/c4-trio'],
    },
    {
        id: 102, name: 'Clases de Yoga', short_name: null,
        description: 'Taller de bienestar.',
        emoji: '🧘', location: 'Hacienda La Trinidad Parque Cultural', place_id: 9,
        sublocation: null, lat: 10.4416, lng: -66.8812,
        tags: ['Taller', 'Bienestar', 'Ejercicio', 'Yoga'],
        occurrences: [[inDays(1), '09:00:00', null, null]],
        urls: ['https://example.org/yoga'],
    },
    {
        id: 103, name: 'Festival de Cine Francés', short_name: null,
        description: 'Ciclo de cine.',
        emoji: '🎬', location: 'Alianza Francesa', place_id: 11,
        sublocation: null, lat: 10.5016, lng: -66.9139,
        tags: ['Cine', 'CineFrances', 'FestivalDeCine'],
        occurrences: [[inDays(4), '18:30:00', null, null]],
        urls: ['https://example.org/cine'],
    },
    // Dropped by the adapter: no coordinates, so it cannot be placed.
    {
        id: 104, name: 'Evento sin ubicación', short_name: null, description: null,
        emoji: null, location: null, place_id: null, sublocation: null,
        lat: null, lng: null, tags: ['Cultura'],
        occurrences: [[inDays(5), '12:00:00', null, null]],
        urls: ['https://example.org/x'],
    },
];

const LOCATIONS = [
    { id: 7, name: 'Centro Cultural Chacao', short_name: 'Chacao', very_short_name: null,
      lat: 10.4946, lng: -66.9019, emoji: '🎭', address: null, description: null,
      website_url: null, tags: [] },
    { id: 9, name: 'Hacienda La Trinidad Parque Cultural', short_name: null, very_short_name: null,
      lat: 10.4416, lng: -66.8812, emoji: '🌳', address: null, description: null,
      website_url: null, tags: [] },
    { id: 11, name: 'Alianza Francesa', short_name: null, very_short_name: null,
      lat: 10.5016, lng: -66.9139, emoji: '🥖', address: null, description: null,
      website_url: null, tags: [] },
];

async function serve(distDir) {
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost');
        let path = join(distDir, normalize(decodeURIComponent(url.pathname)));

        if (url.pathname === '/api/v1/feed/events') {
            res.writeHead(200, { 'content-type': 'application/json' });
            return res.end(JSON.stringify(EVENTS));
        }
        if (url.pathname === '/api/v1/feed/locations') {
            res.writeHead(200, { 'content-type': 'application/json' });
            return res.end(JSON.stringify(LOCATIONS));
        }

        try {
            const info = await stat(path).catch(() => null);
            if (info?.isDirectory()) path = join(path, 'index.html');
            const body = await readFile(path);
            res.writeHead(200, { 'content-type': MIME[extname(path)] || 'application/octet-stream' });
            res.end(body);
        } catch {
            res.writeHead(404).end('not found');
        }
    });
    await new Promise(resolve => server.listen(0, resolve));
    return { server, port: server.address().port };
}

/**
 * Loads one bundle, waits for the data pass to finish, opens both selectors and
 * reports what the DOM shows. `withState` enables the dev-only introspection.
 */
async function checkBundle(distDir, label, { withState }) {
    const errors = [];
    const { server, port } = await serve(distDir);
    const browser = await chromium.launch({ headless: !HEADED });
    const page = await browser.newPage();

    page.on('console', msg => {
        if (msg.type() === 'error') errors.push('console: ' + msg.text());
    });
    page.on('pageerror', err => errors.push('pageerror: ' + err.message));
    page.on('requestfailed', req => {
        // Cross-origin tile requests fail in a sandbox with no network; only
        // same-origin failures are real regressions.
        if (req.url().startsWith(`http://localhost:${port}`)) {
            errors.push('requestfailed: ' + req.url() + ' — ' + req.failure()?.errorText);
        }
    });

    await page.goto(`http://localhost:${port}/`, { waitUntil: 'load' });

    // The loading screen lifts only once the data has been processed. Surface
    // the console trail on timeout: a blank screen is otherwise undiagnosable.
    try {
        await page.waitForSelector('#filter-container:not(.initially-hidden)', { timeout: 25000 });
        await page.waitForFunction(
            () => document.getElementById('map')?.children.length > 0, null, { timeout: 25000 });
    } catch (err) {
        console.error(`\n[${label}] never finished loading:`, err.message);
        for (const line of errors) console.error('  - ' + line);
        await browser.close();
        server.close();
        process.exit(1);
    }

    // The first-visit welcome modal sits over everything.
    if (await page.isVisible('#welcome-modal.show')) {
        await page.keyboard.press('Escape');
        await page.waitForSelector('#welcome-modal:not(.show)', { timeout: 5000 });
    }

    // Both selectors are popovers that render on open, and they render
    // checkbox rows rather than buttons.
    for (const [button, panel] of [['#tag-selector-button', 'tag-selector-panel'],
                                   ['#format-selector-button', 'format-selector-panel']]) {
        await page.click(button);
        await page.waitForFunction(
            id => document.getElementById(id)?.querySelectorAll('label').length > 0,
            panel, { timeout: 10000 });
        await page.keyboard.press('Escape');
    }

    const summary = await page.evaluate(withState => {
        const s = withState ? window.__fomo?.state : null;
        const rows = id => [...document.getElementById(id)?.querySelectorAll('label') || []]
            .map(b => b.textContent.trim()).filter(Boolean);
        return {
            eventCount: s?.allEvents?.length ?? null,
            locationKeys: Object.keys(s?.locationsByLatLng || {}).length,
            curatedTags: s?.hierarchyTagsSet ? [...s.hierarchyTagsSet].length : null,
            eventTypes: (s?.allEvents || []).map(e => `${e.id}:${e.event_type}`).sort(),
            canonicalTags: (s?.allEvents || []).map(e => `${e.id}:${(e.tags || []).join('|')}`).sort(),
            tagSelectorRows: rows('tag-selector-panel'),
            formatSelectorRows: rows('format-selector-panel'),
            sheetPresent: !!document.getElementById('sheet'),
            mapHasCanvas: !!document.querySelector('#map canvas'),
            // Markers are drawn from the event's emoji: with no artwork packs
            // bundled, this is the fallback path, so assert it produced images.
            iconImages: document.querySelectorAll('.event-icon img').length,
        };
    }, withState);

    await browser.close();
    server.close();
    return { summary, errors };
}

async function main() {
    if (!existsSync(join(PROD_DIST, 'index.html'))) {
        console.error('dist/ is missing — run `npm run build` first.');
        process.exit(1);
    }

    // Dev build into a throwaway dir: identical sources, unminified, plus the
    // window.__fomo handle the state assertions read.
    const devDist = mkdtempSync(join(tmpdir(), 'fomoccs-smoke-'));
    execFileSync('node', [join(ROOT, 'build.js'), '--dev'],
        { cwd: ROOT, stdio: 'ignore', env: { ...process.env, DIST_DIR: devDist } });

    let prod, dev;
    try {
        prod = await checkBundle(PROD_DIST, 'prod', { withState: false });
        dev = await checkBundle(devDist, 'dev', { withState: true });
    } finally {
        rmSync(devDist, { recursive: true, force: true });
    }

    const failures = [];
    const eq = (label, actual, expected) => {
        if (JSON.stringify(actual) !== JSON.stringify(expected)) {
            failures.push(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
        }
    };

    // --- Production: the artefact that deploys ---
    failures.push(...prod.errors.map(e => `[prod] ${e}`));
    for (const [label, summary] of [['prod', prod.summary], ['dev', dev.summary]]) {
        if (!summary.tagSelectorRows.length) failures.push(`[${label}] tag selector rendered no rows`);
        if (!summary.formatSelectorRows.length) failures.push(`[${label}] format selector rendered no rows`);
        if (!summary.sheetPresent) failures.push(`[${label}] event sheet missing`);
        if (!summary.mapHasCanvas) failures.push(`[${label}] map never drew a canvas`);
        if (!summary.iconImages) failures.push(`[${label}] no emoji icons were rasterized`);
    }

    // --- Data layer (dev build only) ---
    const s = dev.summary;
    // The adapter drops the event with no coordinates; the other three load.
    eq('events loaded', s.eventCount, 3);
    eq('venues keyed', s.locationKeys, 3);
    // Formats are derived from tags at runtime: there is no event_type column.
    eq('derived event formats', s.eventTypes, ['101:Concierto', '102:Curso', '103:Cine']);
    // Raw extractor spellings canonicalize onto the curated taxonomy.
    eq('canonical tags', s.canonicalTags, [
        '101:Latina|Salsa',
        '102:Cursos|Bienestar|Ejercicio|Yoga',
        '103:Cine|Francés',
    ]);
    if (s.curatedTags < 18) failures.push(`curated taxonomy too small: ${s.curatedTags}`);
    for (const root of ['Música', 'Cine', 'Bienestar']) {
        if (!s.tagSelectorRows.includes(root)) {
            failures.push(`tag selector missing root "${root}" (got ${s.tagSelectorRows.join(', ')})`);
        }
    }
    for (const format of ['Concierto', 'Cine', 'Curso']) {
        if (!s.formatSelectorRows.includes(format)) {
            failures.push(`format selector missing "${format}" (got ${s.formatSelectorRows.join(', ')})`);
        }
    }
    failures.push(...dev.errors.map(e => `[dev] ${e}`));

    if (failures.length) {
        console.error('\nSMOKE FAILED:');
        for (const line of failures) console.error('  - ' + line);
        process.exit(1);
    }

    console.log('production bundle : clean load, no console errors');
    console.log('dev bundle        :', JSON.stringify(dev.summary, null, 2));
    console.log('\nSMOKE PASSED');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});