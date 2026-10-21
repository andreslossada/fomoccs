const esbuild = require('esbuild');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const yaml = require('yaml');

const SRC = path.join(__dirname, 'src');
// DIST_DIR lets a caller redirect the output (the smoke test builds a dev
// bundle into a temp dir so it can introspect window.__fomo without shipping
// that handle in production).
const DIST = process.env.DIST_DIR
    ? path.resolve(process.env.DIST_DIR)
    : path.join(__dirname, 'dist');
const FLATPICKR_JS = path.join(__dirname, 'node_modules', 'flatpickr', 'dist', 'flatpickr.js');
const FLATPICKR_CSS = path.join(__dirname, 'node_modules', 'flatpickr', 'dist', 'flatpickr.css');

// City/region config (config/<FOMO_CITY>.yaml, default "caracas"). Its
// `frontend` block is the single source of truth for the built site: it is
// injected as `window.__CITY__` and replaces the {{TOKEN}} placeholders in
// index.html.
const FOMO_CITY = process.env.FOMO_CITY || 'caracas';
const CITY_CONFIG_PATH = path.join(__dirname, 'config', `${FOMO_CITY}.yaml`);

// Directories to include in dist/ (everything the server needs)
const ASSET_DIRS = ['data', 'images', 'fonts', 'vendor'];

function copyDirSync(src, dest) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
        const srcPath = path.join(src, entry.name);
        const destPath = path.join(dest, entry.name);
        if (entry.isDirectory()) {
            copyDirSync(srcPath, destPath);
        } else {
            fs.copyFileSync(srcPath, destPath);
        }
    }
}

/**
 * Remove dist/ and recreate it empty.
 *
 * A dev build drops symlinks for the asset dirs so edits show up without a
 * rebuild. `fs.rmSync(DIST, {recursive: true})` does not reliably clear those
 * on Windows — a leftover link makes the next prod build fail part-way through
 * with ENOENT, because Node resolves the link as a regular file containing the
 * target path. Unlink any symlink explicitly before falling back to a recursive
 * delete so switching dev -> prod always starts from a clean slate.
 */
function cleanDist() {
    if (!fs.existsSync(DIST)) {
        fs.mkdirSync(DIST, { recursive: true });
        return;
    }

    for (const entry of fs.readdirSync(DIST, { withFileTypes: true })) {
        if (entry.isSymbolicLink()) {
            fs.unlinkSync(path.join(DIST, entry.name));
        }
    }

    fs.rmSync(DIST, { recursive: true, force: true });
    fs.mkdirSync(DIST, { recursive: true });
}

/**
 * Load and validate the city config.
 *
 * A missing or incomplete config fails the build rather than shipping a site
 * that points at the wrong coordinates or renders a selector with no roots.
 */
function loadCityConfig() {
    if (!fs.existsSync(CITY_CONFIG_PATH)) {
        throw new Error(`config/${FOMO_CITY}.yaml not found — set FOMO_CITY or add the file`);
    }
    const cfg = yaml.parse(fs.readFileSync(CITY_CONFIG_PATH, 'utf8')) || {};
    const fe = cfg.frontend || {};
    if (!fe.map || !fe.map.center) {
        throw new Error(`config/${FOMO_CITY}.yaml is missing a frontend.map.center`);
    }
    if (!fe.filter_roots?.tag?.length) {
        throw new Error(`config/${FOMO_CITY}.yaml needs a non-empty frontend.filter_roots.tag`);
    }
    return cfg;
}

/**
 * Runtime values injected as a `window.__CITY__` global at the head of the JS
 * bundle, so they exist before any IIFE module evaluates.
 */
function cityPrelude(fe, isDev) {
    const jsSubset = {
        map: {
            center: fe.map.center,
            zoom: fe.map.zoom,
            dot_color: fe.map.dot_color,
        },
        timezone: fe.timezone,
        cityId: FOMO_CITY,
        areaIds: fe.area_ids || {},
        neighborhoodSelector: fe.neighborhood_selector || {},
        venueSelector: fe.venue_selector || {},
        filterRoots: fe.filter_roots || {},
        domain: fe.domain,
        swEnabled: !isDev && fe.sw_enabled !== false,
    };
    return `window.__CITY__ = ${JSON.stringify(jsSubset)};\n`;
}

/**
 * Replace {{TOKEN}} branding placeholders, then fail the build on any leftover
 * token. A silent leftover renders as literal `{{SITE_NAME}}` in the title,
 * which is easy to miss and hard to trace back to a config omission.
 */
function applyBranding(html, fe) {
    const tokens = {
        '{{SITE_NAME}}': fe.site_name,
        '{{DOMAIN}}': fe.domain,
        '{{EMOJI}}': fe.emoji,
        '{{WELCOME_MODAL}}': fe.welcome_modal,
    };
    for (const [token, value] of Object.entries(tokens)) {
        html = html.split(token).join(value);
    }
    const leftover = html.match(/\{\{[A-Z_]+\}\}/);
    if (leftover) {
        throw new Error(`Unreplaced branding token ${leftover[0]} — add it to config/${FOMO_CITY}.yaml frontend block`);
    }
    return html;
}

/**
 * Make `link` point at `target` without copying when the platform allows it.
 *
 * On Windows a plain symlink needs elevated rights or developer mode, and a
 * target written as a POSIX path (/c/Users/...) does not resolve at all — the
 * directory then 404s for every asset. A junction needs no special rights and
 * always stores an absolute Windows path, so it is the right tool there.
 * Everywhere else a directory symlink is fine.
 *
 * If the link cannot be created at all, copy instead: a slower build beats a
 * build whose assets are missing.
 */
function linkOrCopy(target, link) {
    // realpath resolves any symlinked prefix, so the stored target is a path
    // the OS can actually open.
    const resolved = (() => {
        try { return fs.realpathSync(target); } catch (_) { return path.resolve(target); }
    })();
    try {
        fs.symlinkSync(resolved, link, process.platform === 'win32' ? 'junction' : 'dir');
    } catch (err) {
        console.warn(`  could not link ${link} (${err.code || err.message}); copying instead`);
        copyDirSync(target, link);
    }
}

async function build(isDev) {
    const startTime = Date.now();

    const cfg = loadCityConfig();
    const frontend = cfg.frontend;

    // Clean dist/
    cleanDist();

    // Parse index.html to extract JS file list in load order
    const htmlSource = fs.readFileSync(path.join(SRC, 'index.html'), 'utf8');
    const jsFileRegex = /<script\s+src="(js\/[^"?]+)/g;
    const jsFiles = [];
    let match;
    while ((match = jsFileRegex.exec(htmlSource)) !== null) {
        jsFiles.push(match[1]);
    }

    if (jsFiles.length === 0) {
        throw new Error('No JS files found in index.html');
    }

    // Inject API_BASE_URL from environment (defaults to '' for relative URLs in
    // local dev). The backend URL is Cloud Run's, resolved by the deploy script
    // and passed in as API_BASE_URL — the frontend reads events from the live
    // API, not from static exports.
    const apiBaseUrl = process.env.API_BASE_URL || '';
    const apiBaseUrlDecl = `var API_BASE_URL = ${JSON.stringify(apiBaseUrl)};\n`;

    // Concatenate JS: city config, API base, flatpickr, then app modules in order
    const flatpickrJs = fs.readFileSync(FLATPICKR_JS, 'utf8');
    let originalJsSize = 0;
    const appJs = jsFiles.map(f => {
        const filePath = path.join(SRC, f);
        const content = fs.readFileSync(filePath, 'utf8');
        originalJsSize += Buffer.byteLength(content, 'utf8');
        return content;
    }).join('\n;\n');
    const concatenated = cityPrelude(frontend, isDev)
        + apiBaseUrlDecl
        + flatpickrJs + '\n;\n' + appJs;

    // Minify JS in prod, pass through in dev
    let jsContent;
    if (isDev) {
        // Dev-only: expose the App orchestrator (and via it state.map,
        // datePickerInstance, filtered-event arrays) on window for browser
        // verification. App is scoped inside the DOMContentLoaded closure, so
        // inject the handle there where it's in scope. Never in prod.
        jsContent = concatenated.replace(
            'App.init();',
            'App.init();\n    if (typeof window !== "undefined") window.__fomo = App;'
        );
    } else {
        const jsResult = await esbuild.transform(concatenated, {
            minify: true,
            target: ['es2020'],
        });
        jsContent = jsResult.code;
    }

    // Bundle CSS (esbuild resolves @import natively)
    const cssResult = await esbuild.build({
        entryPoints: [path.join(SRC, 'css', 'index.css')],
        bundle: true,
        minify: !isDev,
        write: false,
        external: ['*.ttf', '*.woff2'],
    });

    // Prepend flatpickr CSS
    const flatpickrCss = fs.readFileSync(FLATPICKR_CSS, 'utf8');
    let flatpickrCssOut;
    if (isDev) {
        flatpickrCssOut = flatpickrCss;
    } else {
        flatpickrCssOut = (await esbuild.transform(flatpickrCss, { loader: 'css', minify: true })).code;
    }
    let cssContent = flatpickrCssOut + cssResult.outputFiles[0].text;

    // Fix font path: source uses ../fonts/ (relative to css/), but bundled CSS is at root level
    cssContent = cssContent.replace(/\.\.\/fonts\//g, 'fonts/');

    // Generate filenames (content-hashed for prod, stable for dev)
    let jsBundleName, cssBundleName;
    if (isDev) {
        jsBundleName = 'app.js';
        cssBundleName = 'app.css';
    } else {
        const jsHash = crypto.createHash('md5').update(jsContent).digest('hex').slice(0, 8);
        const cssHash = crypto.createHash('md5').update(cssContent).digest('hex').slice(0, 8);
        jsBundleName = `app.${jsHash}.js`;
        cssBundleName = `app.${cssHash}.css`;
    }

    // Write bundles
    fs.writeFileSync(path.join(DIST, jsBundleName), jsContent);
    fs.writeFileSync(path.join(DIST, cssBundleName), cssContent);

    // Transform index.html
    let html = htmlSource;

    // Remove flatpickr CDN references (now bundled)
    html = html.replace(/\s*<link\s+rel="stylesheet"\s+href="https:\/\/cdn\.jsdelivr\.net\/npm\/flatpickr[^>]*>\s*\n?/, '\n');
    html = html.replace(/\s*<script\s+src="https:\/\/cdn\.jsdelivr\.net\/npm\/flatpickr[^>]*><\/script>\s*\n?/, '\n');

    // Replace CSS link with bundle
    html = html.replace(
        /<link\s+rel="stylesheet"\s+href="css\/index\.css">/,
        `<link rel="stylesheet" href="${cssBundleName}">`
    );

    // Replace all individual JS script tags with single bundle
    html = html.replace(
        /\n\s*<!-- Core -->[\s\S]*<script src="js\/[^"]*"><\/script>\s*/,
        `\n\n    <script src="${jsBundleName}"></script>\n`
    );

    // Inject city branding (must run last; fails the build on any leftover token)
    html = applyBranding(html, frontend);

    fs.writeFileSync(path.join(DIST, 'index.html'), html);

    // Copy static HTML pages
    for (const page of ['about.html', 'privacy.html']) {
        const pagePath = path.join(SRC, page);
        if (fs.existsSync(pagePath)) {
            fs.copyFileSync(pagePath, path.join(DIST, page));
        }
    }

    // Copy asset directories into dist/
    // Dev: link them (fast, live updates); Prod: full copies (self-contained)
    for (const dir of ASSET_DIRS) {
        const target = path.join(SRC, dir);
        const link = path.join(DIST, dir);
        if (!fs.existsSync(target)) continue;
        if (isDev) {
            linkOrCopy(target, link);
        } else {
            copyDirSync(target, link);
        }
    }

    // Print summary
    const jsBundleSize = Buffer.byteLength(jsContent, 'utf8');
    const cssBundleSize = Buffer.byteLength(cssContent, 'utf8');
    const elapsed = Date.now() - startTime;
    const mode = isDev ? 'dev' : 'prod';

    console.log(`\n[${mode}] Build complete in ${elapsed}ms\n`);
    if (isDev) {
        console.log(`  JS:  ${jsFiles.length} files → ${(jsBundleSize / 1024).toFixed(1)} KB (${jsBundleName})`);
    } else {
        console.log(`  JS:  ${jsFiles.length} files, ${(originalJsSize / 1024).toFixed(1)} KB → ${(jsBundleSize / 1024).toFixed(1)} KB (${Math.round((1 - jsBundleSize / originalJsSize) * 100)}% smaller)`);
    }
    console.log(`  CSS: ${(cssBundleSize / 1024).toFixed(1)} KB (${cssBundleName})`);
    console.log(`  City: ${FOMO_CITY} (${frontend.site_name})`);
    console.log(`  Output: dist/\n`);
}

// Watch mode: rebuild on file changes in src/js/ and src/css/
function watch() {
    let buildTimeout = null;
    const rebuild = () => {
        if (buildTimeout) clearTimeout(buildTimeout);
        buildTimeout = setTimeout(async () => {
            try {
                await build(true);
            } catch (err) {
                console.error('Build error:', err.message);
            }
        }, 50);
    };

    const watchDirs = [
        path.join(SRC, 'js'),
        path.join(SRC, 'css'),
    ];

    for (const dir of watchDirs) {
        fs.watch(dir, { recursive: true }, (event, filename) => {
            if (filename && /\.(js|css)$/.test(filename)) {
                console.log(`  Changed: ${filename}`);
                rebuild();
            }
        });
    }

    // Also watch index.html for structural changes
    fs.watch(path.join(SRC, 'index.html'), () => {
        console.log('  Changed: index.html');
        rebuild();
    });

    // And the city config, so a branding change needs no manual restart
    if (fs.existsSync(CITY_CONFIG_PATH)) {
        fs.watch(CITY_CONFIG_PATH, () => {
            console.log(`  Changed: config/${FOMO_CITY}.yaml`);
            rebuild();
        });
    }

    console.log('Watching for changes... (Ctrl+C to stop)\n');
}

// CLI
const args = process.argv.slice(2);
const isDev = args.includes('--dev');
const isWatch = args.includes('--watch');

build(isDev)
    .then(() => {
        if (isWatch) watch();
    })
    .catch(err => {
        console.error('Build failed:', err);
        process.exit(1);
    });