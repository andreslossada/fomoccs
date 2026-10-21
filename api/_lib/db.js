/**
 * Shared Postgres access for the Vercel functions.
 *
 * Why the feed is served from here instead of the FastAPI backend
 * ---------------------------------------------------------------
 * The frontend is deployed to Vercel and reads its data from the API. The
 * API in production is a Cloud Run service that still points at the retired
 * Cloud SQL instance, so it answers 503 and the map comes up empty. The
 * database itself lives on Supabase (see backend/.env), and the API code
 * reads it fine.
 *
 * These functions are the bridge: they run the same queries the FastAPI
 * feed runs, against the same database, and answer the same paths. They
 * exist so the site works while the Cloud Run service is being re-pointed
 * at Supabase and redeployed.
 *
 * When that is done, delete this directory and the API_BASE_URL variable
 * again — nothing else has to change.
 *
 * Keep the queries in sync with backend/api/routers/feed.py: the frontend
 * reads a flat JSON array and depends on the field names.
 */
const { Pool } = require('pg');

let pool = null;

function getPool() {
    if (pool) return pool;
    const { DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS } = process.env;
    if (!DB_HOST || !DB_NAME || !DB_USER || !DB_PASS) {
        throw new Error(
            'Missing database configuration: DB_HOST, DB_NAME, DB_USER and DB_PASS must be set.'
        );
    }
    pool = new Pool({
        host: DB_HOST,
        port: Number(DB_PORT || 5432),
        database: DB_NAME,
        user: DB_USER,
        password: DB_PASS,
        ssl: { rejectUnauthorized: false },
        // A function instance is reused across requests, but not forever:
        // the pool must not keep sockets open past the instance's life.
        max: 2,
        idleTimeoutMillis: 10_000,
        connectionTimeoutMillis: 10_000,
    });
    // Without a listener, an idle client error takes the process down.
    pool.on('error', (err) => {
        console.error('Unexpected error on an idle client', err);
    });
    return pool;
}

/** Runs a query and returns its rows. */
async function query(text, params = []) {
    const result = await getPool().query(text, params);
    return result.rows;
}

/**
 * Sends a JSON response with the headers the frontend expects.
 *
 * The feed is public information, so the CORS policy is open: the frontend
 * is served from a different origin than the API in local development.
 *
 * Uses the plain Node response API rather than Vercel's `res.status().send()`
 * chain, so the same handler runs under a plain HTTP server in tests.
 */
function sendJson(res, status, body) {
    res.setHeader('content-type', 'application/json; charset=utf-8');
    res.setHeader('access-control-allow-origin', '*');
    res.setHeader('cache-control', 'no-cache');
    res.statusCode = status;
    res.end(JSON.stringify(body));
}

/** Rejects the methods an endpoint does not implement. */
function methodNotAllowed(res, allowed) {
    res.setHeader('allow', allowed);
    sendJson(res, 405, { detail: `Method not allowed. Use ${allowed}.` });
}

module.exports = { query, sendJson, methodNotAllowed };
