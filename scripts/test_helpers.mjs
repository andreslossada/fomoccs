/**
 * Shared helpers for the frontend test scripts.
 *
 * One fixture, one static server, two suites:
 *   scripts/smoke_frontend.mjs   — the bundle loads and the data layer works
 *   scripts/interaction_test.mjs — the UI responds to real interaction
 *
 * Both run against the stubbed feed by default, so they need no backend. Pass
 * --api <url> to point them at a running API instead (the fixtures are then
 * only used for the parts the real API does not serve).
 */
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('..', import.meta.url));

const MIME = {
    '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
    '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
    '.pbf': 'application/x-protobuf', '.png': 'image/png',
};

/** Reads --api <url> from the command line. */
export function argValue(name, fallback = null) {
    const i = process.argv.indexOf(name);
    return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const TODAY = new Date();
const iso = d => d.toISOString().slice(0, 10);
const inDays = n => {
    const d = new Date(TODAY);
    d.setDate(d.getDate() + n);
    return iso(d);
};

/**
 * Three events across three venues, so venue grouping, the recurring-event
 * merge, the tag hierarchy and the format derivation all have something to
 * chew on. Tags use the raw spellings the extractor actually produces, so the
 * canonicalization is exercised too.
 */
export const EVENTS = [
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

export const LOCATIONS = [
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

/**
 * Builds a dev bundle of the frontend into a throwaway directory.
 *
 * A dev build rather than the production one because it exposes
 * `window.__fomo`, which the assertions use to read application state.
 * Production deliberately does not expose that handle.
 */
export function buildDevBundle(apiBaseUrl) {
    const dir = mkdtempSync(join(tmpdir(), 'fomoccs-test-'));
    execFileSync('node', [join(ROOT, 'build.js'), '--dev'], {
        cwd: ROOT,
        stdio: 'ignore',
        env: { ...process.env, DIST_DIR: dir, API_BASE_URL: apiBaseUrl || '' },
    });
    return dir;
}

/**
 * Serves a built bundle, stubbing the feed unless a real API is given.
 *
 * @param {string} distDir - the directory to serve
 * @param {string|null} apiBase - real API base URL, or null to use fixtures
 */
export async function serve(distDir, apiBase = null) {
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost');

        if (url.pathname === '/api/v1/feed/events') {
            if (apiBase) return proxy(res, apiBase + url.pathname);
            res.writeHead(200, { 'content-type': 'application/json' });
            return res.end(JSON.stringify(EVENTS));
        }
        if (url.pathname === '/api/v1/feed/locations') {
            if (apiBase) return proxy(res, apiBase + url.pathname);
            res.writeHead(200, { 'content-type': 'application/json' });
            return res.end(JSON.stringify(LOCATIONS));
        }

        try {
            const path = join(distDir, normalize(decodeURIComponent(url.pathname)));
            const info = await stat(path).catch(() => null);
            const file = info?.isDirectory() ? join(path, 'index.html') : path;
            const body = await readFile(file);
            res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' });
            res.end(body);
        } catch {
            res.writeHead(404).end('not found');
        }
    });
    await new Promise(resolve => server.listen(0, resolve));
    return { server, port: server.address().port };
}

function proxy(res, target) {
    return fetch(target)
        .then(r => r.text())
        .then(body => {
            res.writeHead(200, { 'content-type': 'application/json' });
            res.end(body);
        })
        .catch(err => {
            res.writeHead(502).end(String(err));
        });
}
