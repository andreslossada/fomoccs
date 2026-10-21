/**
 * GET /api/v1/feed/locations — the venues the events are placed at.
 *
 * Mirrors backend/api/routers/feed.py::feed_locations: geocoded, not
 * deleted, with their tags.
 */
const { query, sendJson, methodNotAllowed } = require('../../_lib/db');

const LOCATIONS_SQL = `
select
    l.id,
    l.name,
    l.short_name,
    l.very_short_name,
    l.lat::float8 as lat,
    l.lng::float8 as lng,
    l.emoji,
    l.address,
    l.description,
    l.website_url,
    coalesce(
        (select json_agg(t.name order by t.name)
         from location_tags lt join tags t on t.id = lt.tag_id
         where lt.location_id = l.id),
        '[]'::json
    ) as tags
from locations l
where l.lat is not null
  and l.lng is not null
  and l.deleted_at is null
order by l.id
`;

module.exports = async function handler(req, res) {
    if (req.method !== 'GET') return methodNotAllowed(res, 'GET');
    try {
        const rows = await query(LOCATIONS_SQL);
        sendJson(res, 200, rows);
    } catch (err) {
        console.error('feed/locations failed', err);
        sendJson(res, 500, { detail: 'Could not load the locations feed.' });
    }
};
