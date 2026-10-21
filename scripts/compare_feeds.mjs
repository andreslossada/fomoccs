/**
 * Compares the Vercel feed functions with the FastAPI feed.
 *
 * The two implementations serve the same paths from the same database, and
 * the frontend depends on the exact field names. This is the check that keeps
 * them from drifting apart: it runs the same query both ways and compares the
 * results field by field.
 *
 * It needs a running API and a database:
 *
 *   # terminal 1 — the API
 *   cd backend && uv run uvicorn api.main:app --port 8000
 *   # terminal 2
 *   node scripts/compare_feeds.mjs
 *   node scripts/compare_feeds.mjs --api http://127.0.0.1:8000
 *
 * The database configuration comes from backend/.env.
 */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const ROOT = process.cwd();
const require = createRequire(join(ROOT, 'noop.js'));

const arg = (name, fallback) => {
    const i = process.argv.indexOf(name);
    return i > -1 ? process.argv[i + 1] : fallback;
};
const API = arg('--api', 'http://127.0.0.1:8000');

// The functions read the database configuration from the environment.
for (const line of readFileSync(join(ROOT, 'backend', '.env'), 'utf8').split('\n')) {
    const match = line.match(/^([A-Z_]+)=(.*)$/);
    if (match) process.env[match[1]] = match[2].trim();
}

const { query } = require('./api/_lib/db.js');
const events = require('./api/v1/feed/events.js');
const locations = require('./api/v1/feed/locations.js');

/** Calls a handler and returns what it would send. */
async function call(handler) {
    let body = null;
    const res = {
        statusCode: 200,
        setHeader() {},
        end(payload) { body = payload; },
    };
    await handler({ method: 'GET' }, res);
    return JSON.parse(body);
}

const fromFunctions = {
    events: await call(events),
    locations: await call(locations),
};

const fromApi = {
    events: await (await fetch(`${API}/api/v1/feed/events`)).json(),
    locations: await (await fetch(`${API}/api/v1/feed/locations`)).json(),
};

let problems = 0;
const fail = (message) => {
    console.error(`  ✗ ${message}`);
    problems += 1;
};

for (const kind of ['events', 'locations']) {
    const fn = fromFunctions[kind];
    const api = fromApi[kind];
    console.log(`${kind}: functions=${fn.length} api=${api.length}`);
    if (fn.length !== api.length) {
        fail(`${kind}: different number of records`);
        continue;
    }
    if (!fn.length) {
        fail(`${kind}: no records — is the database populated?`);
        continue;
    }

    const fnKeys = Object.keys(fn[0]).sort().join(',');
    const apiKeys = Object.keys(api[0]).sort().join(',');
    if (fnKeys !== apiKeys) {
        fail(`${kind}: different fields\n      functions: ${fnKeys}\n      api:       ${apiKeys}`);
        continue;
    }

    const byId = new Map(api.map((row) => [row.id, row]));
    let mismatch = null;
    for (const row of fn) {
        const other = byId.get(row.id);
        if (!other) {
            mismatch = `record ${row.id} is missing from the API`;
            break;
        }
        for (const key of Object.keys(row)) {
            if (JSON.stringify(row[key]) !== JSON.stringify(other[key])) {
                mismatch = `record ${row.id} field "${key}" differs: ${JSON.stringify(row[key])} vs ${JSON.stringify(other[key])}`;
                break;
            }
        }
        if (mismatch) break;
    }
    if (mismatch) fail(`${kind}: ${mismatch}`);
    else console.log(`  ✓ all ${fn.length} records match field for field`);
}

if (problems) {
    console.error('\nThe two feeds differ — the frontend would see different data depending on which one answers.');
    process.exit(1);
}
console.log('\nThe two feeds are identical.');
