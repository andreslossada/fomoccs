"""Fill in missing coordinates for locations so venues show up on the map.

Only targets locations that are missing ``lat``/``lng`` — existing pins are
never touched, so curated and manually-corrected coordinates stay put.

Resolution order per location:

1. ``api.services.known_venues`` — the curated Caracas venue table. Free,
   instant, and the most accurate source we have (coordinates verified by
   hand). Matched on the normalized name, then on aliases.
2. Google Places (New) — best coverage of Caracas-specific venues.
3. Geoapify — free-tier safety net.
4. Nominatim (OpenStreetMap) — key-free last resort, rate limited to one
   request per second as its usage policy requires.

Nominatim and Geoapify results are dropped when they land outside the
Caracas bounding box, so a venue in Maracay never gets pinned in Caracas.

Usage::

    python scripts/backfill_missing_coords.py            # dry run
    python scripts/backfill_missing_coords.py --apply    # commit
    python scripts/backfill_missing_coords.py --apply --only-with-events
"""

import argparse
import asyncio
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

import asyncpg
import httpx
from dotenv import load_dotenv

# Venue names carry accents; the default Windows console codec cannot
# encode them and would blow up mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from api.services.geocoding import (  # noqa: E402
    CCS_BOUNDS,
    geocode_with_fallback,
    is_within_caracas,
)
from api.services.known_venues import (  # noqa: E402
    _KNOWN_VENUES,
    lookup_known_venue,
)

load_dotenv(os.path.join(_ROOT, "backend", ".env"))

# Nominatim's public instance allows 1 request/second. Stay under it.
NOMINATIM_DELAY_S = 1.1


def curated_match(name: str) -> tuple[float, float, str] | None:
    """Return curated coordinates for a venue name, if we have them.

    Uses the same lookup the merge pipeline uses, so a venue that the
    pipeline could have pinned is pinned here too.
    """
    coords = lookup_known_venue(name)
    if coords is None:
        return None

    lat, lng = coords.lat, coords.lng
    if not is_within_caracas(lat, lng):
        # The curated table carries a few out-of-city entries; never let
        # them pin a venue inside the Caracas bounding box.
        return None
    return lat, lng, coords.address


@dataclass
class Venue:
    id: int
    name: str
    address: str | None
    event_count: int


@dataclass
class Resolved:
    venue: Venue
    lat: float
    lng: float
    address: str
    source: str


def _dsn() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )


async def fetch_missing(only_with_events: bool) -> list[Venue]:
    conn = await asyncpg.connect(_dsn())
    try:
        sql = """
            SELECT l.id, l.name, l.address, COUNT(e.id) AS event_count
            FROM locations l
            LEFT JOIN events e
                   ON e.location_id = l.id AND e.deleted_at IS NULL
            WHERE l.deleted_at IS NULL
              AND (l.lat IS NULL OR l.lng IS NULL)
        """
        if only_with_events:
            sql += " GROUP BY l.id HAVING COUNT(e.id) > 0"
        else:
            sql += " GROUP BY l.id"
        sql += " ORDER BY COUNT(e.id) DESC, l.id"
        rows = await conn.fetch(sql)
    finally:
        await conn.close()
    return [
        Venue(
            id=r["id"],
            name=r["name"],
            address=r["address"],
            event_count=r["event_count"],
        )
        for r in rows
    ]


async def apply_updates(resolved: list[Resolved]) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.executemany(
            "UPDATE locations SET lat=$1, lng=$2, "
            "address=COALESCE(NULLIF($3,''), address) WHERE id=$4",
            [
                (r.lat, r.lng, r.address, r.venue.id)
                for r in resolved
            ],
        )
    finally:
        await conn.close()


async def resolve_all(venues: list[Venue]) -> tuple[list[Resolved], list[Venue]]:
    google_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "")
    user_agent = os.environ.get(
        "NOMINATIM_USER_AGENT", "Fomoccs/1.0 (Caracas events map)"
    )

    resolved: list[Resolved] = []
    unresolved: list[Venue] = []
    last_call = 0.0

    async with httpx.AsyncClient(timeout=20.0) as client:
        for i, venue in enumerate(venues, 1):
            curated = curated_match(venue.name)
            if curated is not None:
                lat, lng, address = curated
                resolved.append(
                    Resolved(venue, lat, lng, address, "curated")
                )
                continue

            # Respect the Nominatim rate limit across every provider call.
            now = asyncio.get_event_loop().time()
            wait = NOMINATIM_DELAY_S - (now - last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            last_call = asyncio.get_event_loop().time()

            result = await geocode_with_fallback(
                venue.name,
                address=venue.address,
                google_api_key=google_key,
                geoapify_api_key=geoapify_key,
                nominatim_enabled=True,
                nominatim_user_agent=user_agent,
                client=client,
            )
            if result is None:
                unresolved.append(venue)
                print(
                    f"  [{i}/{len(venues)}] MISS  {venue.name[:58]}",
                    flush=True,
                )
                continue

            resolved.append(
                Resolved(
                    venue,
                    result.lat,
                    result.lng,
                    result.formatted_address,
                    "geocoder",
                )
            )
            print(
                f"  [{i}/{len(venues)}] ok    {venue.name[:52]:<54} "
                f"-> {result.lat:.6f},{result.lng:.6f}",
                flush=True,
            )

    return resolved, unresolved


async def main(apply: bool, only_with_events: bool) -> None:
    venues = await fetch_missing(only_with_events)
    print(f"Locations missing coordinates: {len(venues)}")
    if not venues:
        return

    resolved, unresolved = await resolve_all(venues)

    by_source: dict[str, int] = {}
    for r in resolved:
        by_source[r.source] = by_source.get(r.source, 0) + 1

    print()
    print("=" * 78)
    print(f"RESOLVED: {len(resolved)}   UNRESOLVED: {len(unresolved)}")
    print(f"  by source: {by_source}")
    print("=" * 78)

    if unresolved:
        print("\nUnresolved (add these to known_venues.py to pin them):")
        for v in unresolved:
            print(f"  id={v.id:<5} events={v.event_count:<4} {v.name!r}")

    if not apply:
        print(f"\n[DRY RUN] {len(resolved)} locations would be updated.")
        print("Re-run with --apply to commit.")
        return

    await apply_updates(resolved)
    print(f"\n[APPLY] Updated {len(resolved)} locations.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the updates")
    parser.add_argument(
        "--only-with-events",
        action="store_true",
        help="only geocode venues that currently have events",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply, only_with_events=args.only_with_events))
