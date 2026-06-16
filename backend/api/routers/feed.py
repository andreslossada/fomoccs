from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from api.dependencies import SessionDep
from api.middleware.rate_limit import feed_rate_limit
from api.models.base import EventStatus
from api.models.event import Event, EventOccurrence
from api.models.location import Location

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("/events")
async def feed_events(
    db: SessionDep,
    _rate: None = Depends(feed_rate_limit),
) -> list[dict[str, object]]:
    """Return events for the public frontend map (flat JSON array)."""
    today = date.today()
    cutoff = today + timedelta(days=90)

    # IDs of events with at least one occurrence between today and cutoff
    event_ids_sq = (
        select(EventOccurrence.event_id)
        .where(EventOccurrence.start_date >= today)
        .where(EventOccurrence.start_date <= cutoff)
        .distinct()
        .subquery()
    )

    stmt = (
        select(Event)
        .where(
            Event.id.in_(select(event_ids_sq.c.event_id)),
            Event.status == EventStatus.active,
            Event.active(),
        )
        .options(
            selectinload(Event.occurrences),
            selectinload(Event.urls),
            selectinload(Event.tags),
            joinedload(Event.location),
        )
    )

    result = await db.scalars(stmt)
    events = result.all()

    out: list[dict[str, object]] = []
    for ev in events:
        loc = ev.location
        occurrences = [
            [
                occ.start_date.isoformat() if occ.start_date else None,
                occ.start_time,
                occ.end_date.isoformat() if occ.end_date else None,
                occ.end_time,
            ]
            for occ in sorted(ev.occurrences, key=lambda o: o.id)
        ]

        out.append(
            {
                "name": ev.name,
                "short_name": ev.short_name,
                "description": ev.description,
                "emoji": ev.emoji,
                "location": loc.name if loc else None,
                "sublocation": ev.sublocation,
                "lat": loc.lat if loc else None,
                "lng": loc.lng if loc else None,
                "tags": [t.name for t in ev.tags],
                "occurrences": occurrences,
                "urls": [u.url for u in sorted(ev.urls, key=lambda u: u.id)],
            }
        )

    return out


@router.get("/locations")
async def feed_locations(
    db: SessionDep,
    _rate: None = Depends(feed_rate_limit),
) -> list[dict[str, object]]:
    """Return locations for the public frontend map (flat JSON array)."""
    stmt = (
        select(Location)
        .where(Location.lat.isnot(None), Location.lng.isnot(None), Location.active())
        .options(selectinload(Location.tags))
    )

    result = await db.scalars(stmt)
    locations = result.all()

    return [
        {
            "name": loc.name,
            "short_name": loc.short_name,
            "very_short_name": loc.very_short_name,
            "lat": loc.lat,
            "lng": loc.lng,
            "emoji": loc.emoji,
            "address": loc.address,
            "description": loc.description,
            "tags": [t.name for t in loc.tags],
        }
        for loc in locations
    ]
