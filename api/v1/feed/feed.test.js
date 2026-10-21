/**
 * Unit tests for the feed functions.
 *
 * These lock the contract the frontend depends on — the paths, the filters in
 * the query, and the failure behaviour — without needing a database. The
 * drift check against the live FastAPI feed lives in
 * scripts/compare_feeds.mjs, which does need one.
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const DB_MODULE = path.join(__dirname, '..', '..', '_lib', 'db.js');

/** Loads a handler with the database module replaced by a stub. */
function loadHandler(modulePath, stub) {
    const resolved = require.resolve(DB_MODULE);
    const previous = require.cache[resolved];
    require.cache[resolved] = {
        id: resolved,
        filename: resolved,
        loaded: true,
        exports: stub,
    };
    try {
        delete require.cache[require.resolve(modulePath)];
        return require(modulePath);
    } finally {
        if (previous) require.cache[resolved] = previous;
        else delete require.cache[resolved];
    }
}

/** Minimal stand-in for the Vercel response object. */
function fakeRes() {
    return {
        statusCode: 200,
        headers: {},
        body: null,
        setHeader(name, value) { this.headers[name] = value; },
        end(payload) { this.body = payload; },
    };
}

test('events: returns the rows as a JSON array', async () => {
    const rows = [{ id: 1, name: 'Un evento', tags: [], occurrences: [], urls: [] }];
    let seenSql = null;
    const handler = loadHandler(path.join(__dirname, 'events.js'), {
        query: async (sql) => { seenSql = sql; return rows; },
        sendJson: (res, status, body) => {
            res.statusCode = status;
            res.end(JSON.stringify(body));
        },
        methodNotAllowed: (res) => { res.statusCode = 405; res.end('{}'); },
    });

    const res = fakeRes();
    await handler({ method: 'GET' }, res);

    assert.strictEqual(res.statusCode, 200);
    assert.deepStrictEqual(JSON.parse(res.body), rows);
    assert.match(seenSql, /from events/i);
    assert.match(seenSql, /e\.status = 'active'/);
    assert.match(seenSql, /e\.deleted_at is null/);
    assert.match(seenSql, /current_date \+ 180/);
    // The field names are the frontend's contract.
    for (const column of ['e.id', 'e.name', 'e.location_id as place_id', 'l.lat', 'l.lng']) {
        assert.ok(seenSql.includes(column), `missing ${column}`);
    }
});

test('locations: returns the rows as a JSON array', async () => {
    const rows = [{ id: 1, name: 'Un lugar', lat: 10, lng: -66, tags: [] }];
    let seenSql = null;
    const handler = loadHandler(path.join(__dirname, 'locations.js'), {
        query: async (sql) => { seenSql = sql; return rows; },
        sendJson: (res, status, body) => {
            res.statusCode = status;
            res.end(JSON.stringify(body));
        },
        methodNotAllowed: (res) => { res.statusCode = 405; res.end('{}'); },
    });

    const res = fakeRes();
    await handler({ method: 'GET' }, res);

    assert.strictEqual(res.statusCode, 200);
    assert.deepStrictEqual(JSON.parse(res.body), rows);
    assert.match(seenSql, /from locations/i);
    assert.match(seenSql, /l\.lat is not null/);
    assert.match(seenSql, /l\.deleted_at is null/);
});

test('a database failure answers 500, not a stack trace', async () => {
    for (const name of ['events.js', 'locations.js']) {
        const handler = loadHandler(path.join(__dirname, name), {
            query: async () => { throw new Error('connection refused'); },
            sendJson: (res, status, body) => {
                res.statusCode = status;
                res.end(JSON.stringify(body));
            },
            methodNotAllowed: (res) => { res.statusCode = 405; res.end('{}'); },
        });
        const res = fakeRes();
        await handler({ method: 'GET' }, res);
        assert.strictEqual(res.statusCode, 500, `${name} should answer 500`);
        assert.match(res.body, /detail/);
    }
});

test('other methods are rejected', async () => {
    const handler = loadHandler(path.join(__dirname, 'events.js'), {
        query: async () => [],
        sendJson: (res, status, body) => {
            res.statusCode = status;
            res.end(JSON.stringify(body));
        },
        methodNotAllowed: (res, allowed) => {
            res.statusCode = 405;
            res.setHeader('allow', allowed);
            res.end('{}');
        },
    });
    const res = fakeRes();
    await handler({ method: 'POST' }, res);
    assert.strictEqual(res.statusCode, 405);
    assert.strictEqual(res.headers.allow, 'GET');
});
