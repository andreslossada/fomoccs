/**
 * Interaction test: drives the real UI and reports what works and what breaks.
 *
 * Where the smoke test checks that the app *loads*, this one checks that it
 * *responds*: the sheet opens with content, the tag selector filters, search
 * finds things, clicking a marker opens its popup, the theme switches, the
 * view can be shared, and the mobile layout puts the sheet at the bottom.
 *
 * Runs against the stubbed feed by default, so it needs no backend. Point it
 * at a real one to exercise real data:
 *
 *   node scripts/interaction_test.mjs
 *   node scripts/interaction_test.mjs --api http://127.0.0.1:8099
 */
import { chromium } from '@playwright/test';
import { rmSync } from 'node:fs';
import { argValue, buildDevBundle, serve } from './test_helpers.mjs';

const API = argValue('--api', null);
const HEADED = process.argv.includes('--headed');

const errors = [];
const results = [];

async function step(name, fn) {
    try {
        const detail = await fn();
        results.push({ name, ok: true, detail });
    } catch (err) {
        results.push({ name, ok: false, detail: err.message.split('\n')[0] });
    }
}

async function main() {
    // The bundle always calls the feed same-origin; the test server proxies
    // those requests to the real API when --api is given. Injecting the real
    // API URL would make every call cross-origin, and the backend's CORS
    // allowlist does not (and should not) include this test's port.
    const dist = buildDevBundle(null);
    const { server, port } = await serve(dist, API);
    const browser = await chromium.launch({ headless: !HEADED });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));

    await page.goto(`http://localhost:${port}/`, { waitUntil: 'load' });
    await page.waitForTimeout(4000);

    await step('carga de datos', async () => {
        const n = await page.evaluate(() => window.__fomo?.state?.allEvents?.length ?? 0);
        if (!n) throw new Error('0 eventos cargados');
        return `${n} eventos`;
    });

    await step('el modal de bienvenida se cierra', async () => {
        const shown = await page.isVisible('#welcome-modal.show');
        await page.keyboard.press('Escape');
        await page.waitForTimeout(500);
        const stillShown = await page.isVisible('#welcome-modal.show');
        if (shown && stillShown) throw new Error('sigue visible tras Escape');
        return shown ? 'cerrado con Escape' : 'no se mostró';
    });

    await step('la hoja de eventos abre con contenido', async () => {
        await page.click('#sheet-handle');
        await page.waitForTimeout(1200);
        const info = await page.evaluate(() => {
            const c = document.getElementById('results-container');
            return { text: (c?.textContent || '').trim().length,
                     labels: [...(c?.querySelectorAll('.section-label') || [])].map(e => e.textContent) };
        });
        if (!info.text) throw new Error('results-container vacío');
        return `${info.text} chars de texto, secciones: ${info.labels.join('/') || '(sin rótulos)'}`;
    });

    await step('el selector de tags filtra', async () => {
        const before = await page.evaluate(() => window.__fomo?.state?.currentlyMatchingEvents?.length ?? 0);
        await page.click('#tag-selector-button');
        await page.waitForTimeout(600);
        const rows = await page.evaluate(() =>
            [...document.querySelectorAll('#tag-selector-panel label')].map(l => l.textContent.trim()));
        const target = rows.find(r => r === 'Música');
        if (!target) throw new Error('no hay fila "Música" en el selector');
        await page.evaluate(() => {
            const label = [...document.querySelectorAll('#tag-selector-panel label')]
                .find(l => l.textContent.trim() === 'Música');
            label.querySelector('input').click();
        });
        await page.waitForTimeout(1500);
        const after = await page.evaluate(() => window.__fomo?.state?.currentlyMatchingEvents?.length ?? 0);
        await page.keyboard.press('Escape');
        if (after >= before) throw new Error(`no filtró (${before} → ${after})`);
        return `${before} → ${after} eventos`;
    });

    await step('la búsqueda funciona', async () => {
        await page.fill('#omni-search-input', 'concierto');
        await page.waitForTimeout(1500);
        const found = await page.evaluate(() => ({
            text: (document.getElementById('results-container')?.textContent || '').trim(),
            n: SearchManager.search('concierto', FilterPanelUI.getDynamicFrequencies(),
                                    TagColorManager.getSelectedTagsWithColors()).length,
        }));
        await page.fill('#omni-search-input', '');
        await page.waitForTimeout(800);
        if (!found.n) throw new Error('búsqueda sin resultados');
        return `${found.n} resultados`;
    });

    await step('un marker abre popup', async () => {
        // Clear the tag filter first: the popup test needs markers on screen,
        // and a narrow filter can leave none.
        await page.click('#tag-selector-button');
        await page.waitForTimeout(500);
        await page.evaluate(() => {
            const label = [...document.querySelectorAll('#tag-selector-panel label')]
                .find(l => l.textContent.trim() === 'Música');
            const input = label?.querySelector('input');
            if (input?.checked) input.click();
        });
        await page.waitForTimeout(1200);
        await page.keyboard.press('Escape');

        // Wait for the camera to settle: a fit-pan can still be animating and
        // would move the marker between picking it and clicking on it.
        await page.waitForFunction(() => !window.__fomo?.state?.map?.isMoving(), null, { timeout: 8000 })
            .catch(() => {});
        const target = await page.evaluate(() => {
            const m = window.__fomo?.state?.map;
            if (!m) return null;
            // The icon glyph is the clickable part; a label is drawn offset from
            // its data point, so clicking the point would miss it. Skip
            // anything under the docked sheet on the left.
            //
            // The map container is deliberately larger than the window
            // (overscan, so the panel can shift the visual centre) and
            // offset. map.project() returns canvas coordinates; the viewport
            // position is the canvas coordinate plus the container's offset.
            const rect = m.getContainer().getBoundingClientRect();
            for (const f of m.queryRenderedFeatures({ layers: ['marker-symbols'] })) {
                if (f.properties?.labelType !== 'icon') continue;
                const p = m.project(f.geometry.coordinates);
                const x = p.x + rect.left, y = p.y + rect.top;
                if (x < 500 || x > innerWidth - 60 || y < 80 || y > innerHeight - 80) continue;
                return { x: Math.round(x), y: Math.round(y) };
            }
            return null;
        });
        if (!target) throw new Error('no hay iconos renderizados');
        await page.mouse.click(target.x, target.y);
        await page.waitForTimeout(1500);
        const popup = await page.evaluate(() => {
            const p = document.querySelector('.maplibregl-popup-content');
            return p ? p.textContent.trim().slice(0, 60) : null;
        });
        if (!popup) throw new Error('no se abrió el popup');
        return `"${popup}"`;
    });

    await step('el tema claro se aplica', async () => {
        await page.keyboard.press('Escape');
        await page.click('#logo-container');
        await page.waitForTimeout(300);
        await page.click('#settings-btn');
        await page.waitForTimeout(500);
        await page.click('#theme-options input[value="light"]');
        await page.waitForTimeout(2000);
        const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
        if (theme !== 'light') throw new Error(`data-theme=${theme}`);
        return `data-theme=${theme}`;
    });

    await step('el mapa conserva sus capas tras cambiar de tema', async () => {
        const info = await page.evaluate(() => {
            const m = window.__fomo?.state?.map;
            if (!m) return { error: 'no hay mapa' };
            const layers = m.getStyle()?.layers?.map(l => l.id) ?? [];
            return { count: layers.length, hasMarkers: layers.includes('marker-symbols') };
        });
        if (info.error) throw new Error(info.error);
        if (!info.hasMarkers) throw new Error('la capa marker-symbols no volvió tras setStyle');
        return `${info.count} capas, markers presente`;
    });

    await step('la vista se puede compartir', async () => {
        await page.keyboard.press('Escape');
        await page.click('#logo-container');
        await page.waitForTimeout(400);
        await page.click('#share-view-btn');
        await page.waitForTimeout(1000);
        // Sharing copies to the clipboard and shows a toast with the URL. The
        // clipboard is unavailable in headless, so read the fallback toast.
        const url = await page.evaluate(() => {
            const toast = document.getElementById('toast-notification')?.textContent?.trim();
            const modal = document.querySelector('.modal.show');
            return toast || (modal ? modal.textContent.trim().slice(0, 120) : null);
        });
        if (!url) throw new Error('no se generó URL de compartir');
        if (!/https?:\/\//.test(url)) throw new Error(`la URL no parece válida: ${url}`);
        return url.slice(0, 110);
    });

    await step('el móvil muestra la hoja inferior', async () => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForTimeout(2000);
        const state = await page.evaluate(() => {
            const sheet = document.getElementById('sheet');
            const r = sheet?.getBoundingClientRect();
            return { exists: !!sheet, height: Math.round(r?.height ?? 0),
                     bottom: Math.round(r?.bottom ?? 0), window: window.innerHeight,
                     handleVisible: !!document.getElementById('sheet-handle')?.offsetParent };
        });
        if (!state.exists) throw new Error('no existe #sheet');
        // On mobile the sheet peeks above the bottom edge as a grip strip; the
        // desktop "show list" handle must not be there.
        if (state.handleVisible) throw new Error('#sheet-handle sigue visible en móvil');
        if (state.bottom !== state.window) throw new Error('la hoja no está pegada al borde inferior');
        return `altura visible ${state.height}px, pegada al borde`;
    });

    console.log('\n=== INTERACCIONES ===');
    for (const r of results) {
        console.log(`${r.ok ? 'OK  ' : 'FALLA'}  ${r.name}${r.detail ? '  →  ' + r.detail : ''}`);
    }
    if (errors.length) {
        console.log('\n=== ERRORES DEL NAVEGADOR ===');
        for (const e of [...new Set(errors)]) console.log('  ' + e);
    }

    await browser.close();
    server.close();
    rmSync(dist, { recursive: true, force: true });

    if (results.some(r => !r.ok)) process.exit(1);
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
