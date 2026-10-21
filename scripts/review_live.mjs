/**
 * Manual review harness: loads the frontend against a real backend and
 * reports what the browser saw — console errors, failed requests, and what
 * the UI actually rendered. Optionally takes screenshots.
 *
 *   node scripts/review_live.mjs --api http://127.0.0.1:8099
 *   node scripts/review_live.mjs --shots C:/out/shots
 *
 * The feed is proxied through the test server, so the app calls it
 * same-origin and the backend's CORS allowlist does not have to know about
 * this port.
 */
import { chromium } from '@playwright/test';
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import { argValue, buildDevBundle, serve } from './test_helpers.mjs';

const API = argValue('--api', 'http://127.0.0.1:8099');
const SHOTS = argValue('--shots', null);

async function main() {
    const dist = buildDevBundle(null);
    const { server, port } = await serve(dist, API);
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const logs = [];
    page.on('console', m => logs.push(`[${m.type()}] ${m.text()}`));
    page.on('pageerror', e => logs.push(`[pageerror] ${e.message}\n${(e.stack || '').split('\n').slice(1, 4).join('\n')}`));
    page.on('requestfailed', r => logs.push(`[requestfailed] ${r.url()} — ${r.failure()?.errorText}`));
    page.on('response', r => {
        if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url()}`);
    });

    await page.goto(`http://localhost:${port}/`, { waitUntil: 'load' });
    await page.waitForTimeout(6000);

    const report = await page.evaluate(() => {
        const s = window.__fomo?.state;
        const canvas = document.querySelector('#map canvas');
        return {
            events: s?.allEvents?.length ?? null,
            locations: Object.keys(s?.locationsByLatLng || {}).length,
            filteredByDate: (s?.allEventsFilteredByDate || []).length,
            matching: (s?.currentlyMatchingEvents || []).length,
            eventTypes: [...new Set((s?.allEvents || []).map(e => e.event_type))].sort(),
            keywords: [...new Set((s?.allEvents || []).flatMap(e => e.keywords || []))].length,
            availableTags: (s?.allAvailableTags || []).length,
            mapSize: canvas ? `${canvas.width}x${canvas.height}` : null,
            icons: document.querySelectorAll('.event-icon img').length,
        };
    });

    console.log(JSON.stringify(report, null, 2));
    console.log('\n--- browser log ---');
    for (const line of logs) console.log(line);

    if (SHOTS) {
        const shot = async (name, ms = 900) => {
            await page.waitForTimeout(ms);
            await page.screenshot({ path: join(SHOTS, name) });
            console.log('  ' + name);
        };
        await page.keyboard.press('Escape');
        await shot('dark.png', 400);
        await page.click('#sheet-handle');
        await shot('sheet.png', 1400);
        await page.keyboard.press('Escape');
        await page.click('#tag-selector-button');
        await shot('tag-selector.png', 900);
        await page.keyboard.press('Escape');
        await page.click('#format-selector-button');
        await shot('format-selector.png', 900);
        await page.keyboard.press('Escape');
        await page.click('#logo-container');
        await page.waitForTimeout(300);
        await page.click('#settings-btn');
        await page.waitForTimeout(400);
        await page.click('#theme-options input[value="light"]');
        await page.waitForTimeout(2200);
        await page.click('#settings-close-btn').catch(() => {});
        await shot('light.png', 600);
    }

    await browser.close();
    server.close();
    rmSync(dist, { recursive: true, force: true });
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
