"""Archive events and venues that fall outside the Caracas metro area.

The merge pipeline drops out-of-city listings as they are processed
(``is_out_of_caracas`` in ``event_merging.py``), but that filter only
applies to newly processed extractions. Events extracted before the filter
existed are still sitting in the database, pinning venues in Maracay,
Valencia, Barquisimeto and elsewhere onto a Caracas-only map.

This script soft-deletes them (``deleted_at``), so the feed stops serving
them while nothing is actually destroyed and the rows stay auditable.

Usage::

    python scripts/purge_out_of_city.py            # dry run
    python scripts/purge_out_of_city.py --apply    # commit
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import asyncpg
from dotenv import load_dotenv

# Venue names carry accents; the default Windows console codec cannot
# encode them and would blow up mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from api.services.event_merging import is_out_of_caracas  # noqa: E402

load_dotenv(os.path.join(_ROOT, "backend", ".env"))


@dataclass
class Hit:
    id: int
    name: str
    detail: str


def _dsn() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )


async def collect() -> tuple[list[Hit], list[Hit]]:
    """Return (out-of-city locations, out-of-city events)."""
    conn = await asyncpg.connect(_dsn())
    try:
        loc_rows = await conn.fetch(
            """
            SELECT l.id, l.name, COUNT(e.id) AS n
            FROM locations l
            LEFT JOIN events e
                   ON e.location_id = l.id AND e.deleted_at IS NULL
            WHERE l.deleted_at IS NULL
            GROUP BY l.id, l.name
            ORDER BY COUNT(e.id) DESC
            """
        )
        bad_locations = [
            Hit(r["id"], r["name"], f"{r['n']} events")
            for r in loc_rows
            if is_out_of_caracas(r["name"])
        ]

        ev_rows = await conn.fetch(
            """
            SELECT e.id, e.name, COALESCE(l.name, '(no location)') AS loc
            FROM events e
            LEFT JOIN locations l ON l.id = e.location_id
            WHERE e.deleted_at IS NULL
            """
        )
        bad_events = [
            Hit(r["id"], r["name"], f"at {r['loc']}")
            for r in ev_rows
            if is_out_of_caracas(r["name"], r["loc"])
        ]
    finally:
        await conn.close()
    return bad_locations, bad_events


async def apply(bad_locations: list[Hit], bad_events: list[Hit]) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            if bad_events:
                await conn.execute(
                    "UPDATE events SET deleted_at = NOW() "
                    "WHERE id = ANY($1::int[])",
                    [e.id for e in bad_events],
                )
            if bad_locations:
                await conn.execute(
                    "UPDATE events SET deleted_at = NOW() "
                    "WHERE deleted_at IS NULL AND location_id = ANY($1::int[])",
                    [loc.id for loc in bad_locations],
                )
                await conn.execute(
                    "UPDATE locations SET deleted_at = NOW() WHERE id = ANY($1::int[])",
                    [loc.id for loc in bad_locations],
                )
    finally:
        await conn.close()


async def main(apply_changes: bool) -> None:
    bad_locations, bad_events = await collect()

    print("=" * 78)
    print(f"OUT-OF-CITY LOCATIONS: {len(bad_locations)}")
    print("=" * 78)
    for h in bad_locations:
        print(f"  id={h.id:<5} {h.detail:<12} {h.name!r}")

    print()
    print("=" * 78)
    print(f"OUT-OF-CITY EVENTS: {len(bad_events)}")
    print("=" * 78)
    for h in bad_events[:60]:
        print(f"  id={h.id:<5} {h.detail:<40} {h.name!r}")
    if len(bad_events) > 60:
        print(f"  ... and {len(bad_events) - 60} more")

    if not apply_changes:
        print(
            f"\n[DRY RUN] Would archive {len(bad_events)} events and "
            f"{len(bad_locations)} locations."
        )
        print("Re-run with --apply to commit.")
        return

    await apply(bad_locations, bad_events)
    print(
        f"\n[APPLY] Archived {len(bad_events)} events and "
        f"{len(bad_locations)} locations."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the changes")
    args = parser.parse_args()
    asyncio.run(main(apply_changes=args.apply))
