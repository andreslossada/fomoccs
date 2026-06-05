"""Re-geocode all active locations using Google Places (with Geoapify fallback).

Prints a before/after diff. Only updates locations where the new coords
differ by more than 50m from the existing ones, to avoid churning stable
results from manual fixes.
"""

import asyncio
import math
import os
import sys
from dataclasses import dataclass

import asyncpg
import httpx
from dotenv import load_dotenv

# Reuse the new geocoding service from the backend
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from api.services.geocoding import geocode_with_fallback  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

DIFF_THRESHOLD_M = 200.0  # only update if diff > 200m
MAX_SANE_DIFF_M = 1_500.0  # anything > 1.5km in Caracas is a wrong match, skip


@dataclass
class Location:
    id: int
    name: str
    address: str | None
    lat: float | None
    lng: float | None


@dataclass
class Diff:
    location: Location
    new_lat: float
    new_lng: float
    new_address: str
    diff_m: float | None  # None if no prior coords
    source: str


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _db_dsn() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )


async def fetch_locations() -> list[Location]:
    conn = await asyncpg.connect(_db_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, name, address, lat, lng FROM locations "
            "WHERE deleted_at IS NULL ORDER BY id"
        )
    finally:
        await conn.close()
    return [
        Location(
            id=r["id"],
            name=r["name"],
            address=r["address"],
            lat=float(r["lat"]) if r["lat"] is not None else None,
            lng=float(r["lng"]) if r["lng"] is not None else None,
        )
        for r in rows
    ]


async def apply_update(diff: Diff) -> None:
    conn = await asyncpg.connect(_db_dsn())
    try:
        await conn.execute(
            "UPDATE locations SET lat=$1, lng=$2, "
            "address=COALESCE(NULLIF($3, ''), address) "
            "WHERE id=$4",
            diff.new_lat, diff.new_lng, diff.new_address, diff.location.id,
        )
    finally:
        await conn.close()


async def re_geocode_all(dry_run: bool = True) -> None:
    google_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "")
    if not google_key:
        print("ERROR: GOOGLE_MAPS_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    locations = await fetch_locations()
    print(f"Found {len(locations)} active locations\n")

    diffs: list[Diff] = []
    skipped: list[tuple[Location, str]] = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for loc in locations:
            result = await geocode_with_fallback(
                loc.name,
                address=loc.address,
                google_api_key=google_key,
                geoapify_api_key=geoapify_key,
                client=client,
            )
            if result is None:
                skipped.append((loc, "no result from any provider"))
                continue

            source = "google" if google_key else "geoapify"

            if loc.lat is None or loc.lng is None:
                diff_m = None
            else:
                diff_m = haversine_m(loc.lat, loc.lng, result.lat, result.lng)

            if diff_m is None or diff_m > DIFF_THRESHOLD_M:
                if diff_m is not None and diff_m > MAX_SANE_DIFF_M:
                    skipped.append(
                        (loc, f"diff {diff_m:.0f}m exceeds sanity check, refusing to update")
                    )
                    continue
                diffs.append(
                    Diff(
                        location=loc,
                        new_lat=result.lat,
                        new_lng=result.lng,
                        new_address=result.formatted_address,
                        diff_m=diff_m,
                        source=source,
                    )
                )

    print("=" * 80)
    print("CHANGES (will update)")
    print("=" * 80)
    for d in diffs:
        old = (
            f"({d.location.lat:.6f}, {d.location.lng:.6f})"
            if d.location.lat is not None
            else "(none)"
        )
        diff_str = f"{d.diff_m:.0f}m" if d.diff_m is not None else "new"
        print(
            f"id={d.location.id:2d} [{d.source:8s}] {d.location.name!r:40s}\n"
            f"  old: {old}\n"
            f"  new: ({d.new_lat:.6f}, {d.new_lng:.6f}) [{diff_str}]\n"
            f"  addr: {d.new_address!r}"
        )

    if skipped:
        print()
        print("=" * 80)
        print("SKIPPED (no update)")
        print("=" * 80)
        for loc, reason in skipped:
            print(f"id={loc.id:2d} {loc.name!r:40s}  {reason}")

    if dry_run:
        print(
            f"\n[DRY RUN] Would update {len(diffs)} locations. "
            "Re-run with --apply to commit."
        )
        return

    print(f"\n[APPLY] Updating {len(diffs)} locations...")
    for d in diffs:
        await apply_update(d)
    print("Done.")


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(re_geocode_all(dry_run=dry))
