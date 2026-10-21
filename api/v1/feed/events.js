/**
 * GET /api/v1/feed/events — the events the map shows.
 *
 * Mirrors backend/api/routers/feed.py::feed_events: the same window, the
 * same filters and the same field names. The frontend reads a flat array and
 * depends on those names.
 */
const { query, sendJson, methodNotAllowed } = require('../../_lib/db');

const EVENTS_SQL = `
with live as (
    -- Events with at least one occurrence inside the published window.
    select distinct e.id
    from events e
    join event_occurrences o on o.event_id = e.id
    where o.start_date >= current_date
      and o.start_date <= current_date + 180
      and e.status = 'active'
      and e.deleted_at is null
)
select
    e.id,
    e.name,
    e.short_name,
    e.description,
    e.emoji,
    l.name as location,
    e.location_id as place_id,
    e.sublocation,
    l.lat::float8 as lat,
    l.lng::float8 as lng,
    coalesce(
        (select json_agg(t.name order by t.name)
         from event_tags et join tags t on t.id = et.tag_id
         where et.event_id = e.id),
        '[]'::json
    ) as tags,
    coalesce(
        (select json_agg(
            json_build_array(o.start_date::text, o.start_time, o.end_date::text, o.end_time)
            order by o.id
         ) from event_occurrences o where o.event_id = e.id),
        '[]'::json
    ) as occurrences,
    coalesce(
        (select json_agg(u.url order by u.id)
         from event_urls u where u.event_id = e.id),
        '[]'::json
    ) as urls
from events e
left join locations l on l.id = e.location_id
where e.id in (select id from live)
order by e.id
`;

module.exports = async function handler(req, res) {
    if (req.method !== 'GET') return methodNotAllowed(res, 'GET');
    try {
        const rows = await query(EVENTS_SQL);
        sendJson(res, 200, rows);
    } catch (err) {
        console.error('feed/events failed', err);
        sendJson(res, 500, { detail: 'Could not load the events feed.' });
    }
};
