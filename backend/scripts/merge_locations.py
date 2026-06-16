"""Merge duplicate locations: move events, alternate_names, and tags
to a canonical location, then soft-delete the duplicate.

Usage:  uv run python backend/scripts/merge_locations.py [--apply]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from api.database import AsyncSessionLocal

# canonical_id -> list of duplicate_ids
MERGE_MAP: dict[int, list[int]] = {
    8: [221, 53, 156, 154, 85, 101, 283, 55],   # Teatro Teresa Carreno
    4: [86],            # Centro Cultural Chacao
    169: [173],         # Centro Cultural de Arte Moderno
    206: [205],         # Hotel Eurobuilding
}


async def merge_locations(dry_run: bool = True) -> int:
    session = AsyncSessionLocal()
    try:
        total_moved = 0
        total_deleted = 0

        for canonical_id, dup_ids in MERGE_MAP.items():
            # Get canonical name
            result = await session.execute(
                text("SELECT name FROM locations WHERE id = :lid"),
                {"lid": canonical_id},
            )
            canonical_name = result.scalar() or f"#{canonical_id}"

            for dup_id in dup_ids:
                result = await session.execute(
                    text("SELECT name FROM locations WHERE id = :lid"),
                    {"lid": dup_id},
                )
                dup_name = result.scalar()
                if dup_name is None:
                    print(f"  SKIP: duplicate #{dup_id} not found")
                    continue

                # Count events to move
                result = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM events "
                        "WHERE location_id = :lid AND deleted_at IS NULL"
                    ),
                    {"lid": dup_id},
                )
                evt_count = result.scalar() or 0

                if not dry_run:
                    # Move events
                    result = await session.execute(
                        text(
                            "UPDATE events SET location_id = :canonical "
                            "WHERE location_id = :dup AND deleted_at IS NULL"
                        ),
                        {"canonical": canonical_id, "dup": dup_id},
                    )
                    moved = result.rowcount

                    # Move alternate_names
                    await session.execute(
                        text(
                            "UPDATE location_alternate_names "
                            "SET location_id = :canonical "
                            "WHERE location_id = :dup"
                        ),
                        {"canonical": canonical_id, "dup": dup_id},
                    )

                    # Move location_tags
                    await session.execute(
                        text(
                            "INSERT INTO location_tags (location_id, tag_id) "
                            "SELECT :canonical, tag_id "
                            "FROM location_tags "
                            "WHERE location_id = :dup "
                            "ON CONFLICT DO NOTHING"
                        ),
                        {"canonical": canonical_id, "dup": dup_id},
                    )

                    # Soft-delete duplicate
                    await session.execute(
                        text(
                            "UPDATE locations SET deleted_at = NOW() "
                            "WHERE id = :dup"
                        ),
                        {"dup": dup_id},
                    )

                else:
                    moved = evt_count

                tag = "[DRY-RUN] " if dry_run else ""
                print(
                    f"  {tag}#{canonical_id} '{canonical_name[:40]}'"
                    f" <- #{dup_id} '{dup_name[:30]}'"
                    f" ({moved} evt)"
                )
                total_moved += moved
                total_deleted += 1

        if not dry_run:
            await session.commit()
            print(
                f"\nCommitted. Moved {total_moved} events,"
                f" deleted {total_deleted} locations."
            )
        else:
            print(
                f"\nDRY RUN -- would move {total_moved} events,"
                f" delete {total_deleted} locations."
            )
            print("Re-run with --apply to commit.")

        # ---- Cleanup: delete malformed locations with no events ----
        result = await session.execute(
            text("""
                SELECT l.id, l.name
                FROM locations l
                LEFT JOIN events e ON e.location_id = l.id AND e.deleted_at IS NULL
                WHERE l.deleted_at IS NULL
                AND l.name LIKE '{%'
                GROUP BY l.id, l.name
                HAVING COUNT(e.id) = 0
            """)
        )
        junk = list(result.mappings().all())
        if junk:
            print(f"\nMalformed locations with 0 events: {len(junk)}")
            for j in junk:
                print(f"  #{j['id']} {j['name'][:80]}")
            if not dry_run:
                jids = [str(j["id"]) for j in junk]
                await session.execute(
                    text(f"""
                        UPDATE locations SET deleted_at = NOW()
                        WHERE id IN ({','.join(jids)})
                    """)
                )
                print(f"  Deleted {len(junk)} malformed locations.")
            else:
                print(f"  (would delete {len(junk)})")

        if not dry_run:
            await session.commit()

        # Show final count
        result = await session.execute(
            text("SELECT COUNT(*) FROM locations WHERE deleted_at IS NULL")
        )
        print(f"Active locations after: {result.scalar()}")
        result = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE deleted_at IS NULL")
        )
        print(f"Active events after: {result.scalar()}")

        return total_moved

    finally:
        await session.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(merge_locations(dry_run=dry))
