/**
 * Smoke test for the built frontend.
 *
 * Serves the build over HTTP (the real deploy shape: a static bucket behind an
 * origin) and loads it in Chromium, then asserts the app came up: no console
 * errors, no failed same-origin requests, and the map plus the two selectors
 * actually rendered.
 *
 * Two bundles are checked:
 *   prod  dist/, the artefact that deploys. DOM-only assertions.
 *   dev   the same sources, unminified, built into a temp dir. The dev build
 *         exposes window.__fomo so the data-layer derivations can be asserted
 *         directly; production deliberately does not expose that handle.
 *
 *   npm test
 *   node scripts/smoke_frontend.mjs --headed
 */
import { chromium } from '@playwright/test';
import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { ROOT, argValue, buildDevBundle, serve } from './test_helpers.mjs';

const HEADED = process.argv.includes('--headed');
const PROD_DIST = join(ROOT, 'dist');

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

    const devDist = buildDevBundle(null);
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
