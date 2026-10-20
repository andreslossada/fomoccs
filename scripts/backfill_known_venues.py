"""Backfill location coordinates from curated ``known_venues`` table.

Updates any active location whose normalized name matches a key in
``backend.api.services.known_venues._KNOWN_VENUES`` and currently has
NULL lat/lng. Idempotent: if a location already has coordinates, this
script leaves it alone (curated values should only win for NULL-coord
locations — manually-fixed coords take precedence).

Usage:
    uv run python scripts/backfill_known_venues.py            # dry run
    uv run python scripts/backfill_known_venues.py --apply    # commit
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# Ensure the backend package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from api.services.known_venues import lookup_known_venue  # noqa: E402


@dataclass
class Location:
    id: int
    name: str
    lat: float | None
    lng: float | None


def _db_dsn() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )


async def fetch_null_locations() -> list[Location]:
    conn = await asyncpg.connect(_db_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, name, lat, lng FROM locations "
            "WHERE deleted_at IS NULL "
            "AND (lat IS NULL OR lng IS NULL) "
            "ORDER BY id"
        )
    finally:
        await conn.close()
    return [
        Location(
            id=r["id"],
            name=r["name"],
            lat=float(r["lat"]) if r["lat"] is not None else None,
            lng=float(r["lng"]) if r["lng"] is not None else None,
        )
        for r in rows
    ]


async def apply_pin(loc_id: int, lat: float, lng: float, address: str) -> None:
    conn = await asyncpg.connect(_db_dsn())
    try:
        await conn.execute(
            "UPDATE locations SET lat=$1, lng=$2, "
            "address=COALESCE(NULLIF($3, ''), address) "
            "WHERE id=$4",
            lat,
            lng,
            address,
            loc_id,
        )
    finally:
        await conn.close()


async def backfill_known_venues(dry_run: bool = True) -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")

    locations = await fetch_null_locations()
    print(f"Found {len(locations)} active locations with NULL coords\n")

    matched = []
    skipped = []
    for loc in locations:
        known = lookup_known_venue(loc.name)
        if known is None:
            skipped.append(loc)
            continue
        matched.append((loc, known))

    print("=" * 80)
    print("KNOWN_VENUES HITS (will pin)")
    print("=" * 80)
    for loc, known in matched:
        print(
            f"id={loc.id:4d} {loc.name!r:50s}\n"
            f"  -> ({known.lat:.6f}, {known.lng:.6f})  {known.address!r}"
        )

    if skipped:
        print()
        print("=" * 80)
        print(f"NOT IN known_venues (n={len(skipped)}) — run backfill_geocode for these")
        print("=" * 80)
        for loc in skipped[:20]:
            print(f"  id={loc.id:4d} {loc.name!r}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")

    if dry_run:
        print(
            f"\n[DRY RUN] Would pin {len(matched)} locations. "
            "Re-run with --apply to commit."
        )
        return

    print(f"\n[APPLY] Pinning {len(matched)} locations...")
    for loc, known in matched:
        await apply_pin(loc.id, known.lat, known.lng, known.address)
    print("Done.")


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(backfill_known_venues(dry_run=dry))
