"""One-shot script: deduplicate events that share the same name + location.

For each group of duplicate events (same name + same location_id),
keeps the one with the most ``EventSource`` rows as canonical and merges
``EventSource``, ``EventUrl``, ``EventOccurrence`` from the others into it,
then soft-deletes the duplicates.

Usage:  uv run python backend/scripts/dedup_events.py [--apply]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from api.database import AsyncSessionLocal


async def dedup_events(dry_run: bool = True) -> int:
    session = AsyncSessionLocal()
    try:
        # ---- 1. Find duplicate groups (same name + location_id) ----
        result = await session.execute(
            text("""
                SELECT name, location_id, COUNT(*) as cnt,
                       ARRAY_AGG(id ORDER BY id) as event_ids
                FROM events
                WHERE deleted_at IS NULL
                GROUP BY name, location_id
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """)
        )
        groups = list(result.mappings().all())
        print(f"Found {len(groups)} duplicate groups\n")

        total_deleted = 0
        total_migrated_sources = 0
        total_migrated_urls = 0
        total_migrated_occurrences = 0

        for group in groups:
            name = group["name"]
            event_ids: list[int] = sorted(group["event_ids"])

            # ---- 2. Pick canonical: most sources, then lowest id ----
            result = await session.execute(
                text("""
                    SELECT e.id
                    FROM events e
                    LEFT JOIN event_sources es ON es.event_id = e.id
                    WHERE e.id = ANY(:ids) AND e.deleted_at IS NULL
                    GROUP BY e.id
                    ORDER BY COUNT(es.id) DESC, e.id ASC
                    LIMIT 1
                """),
                {"ids": event_ids},
            )
            canonical_id: int = result.scalar_one()
            duplicates = [eid for eid in event_ids if eid != canonical_id]

            if not duplicates:
                continue

            dup_placeholders = ",".join(str(x) for x in duplicates)
            s = 0
            u = 0
            o = 0

            # ---- 3. Migrate EventSource -> canonical ----
            # Use INSERT + DELETE pattern to handle intra-group duplicates
            result = await session.execute(
                text(f"""
                    WITH moved AS (
                        INSERT INTO event_sources
                        (event_id, extracted_event_id, source_id,
                         trust_score, is_primary, created_at)
                        SELECT
                            :canonical, extracted_event_id, source_id,
                            trust_score, is_primary, created_at
                        FROM event_sources
                        WHERE event_id IN ({dup_placeholders})
                        ON CONFLICT (event_id, extracted_event_id) DO NOTHING
                        RETURNING id
                    )
                    SELECT COUNT(*) FROM moved
                """),
                {"canonical": canonical_id},
            )
            s = result.scalar() or 0

            # ---- 4. Migrate EventUrl -> canonical (skip dupes) ----
            result = await session.execute(
                text(f"""
                    INSERT INTO event_urls (event_id, url)
                    SELECT :canonical, eu.url
                    FROM event_urls eu
                    WHERE eu.event_id IN ({dup_placeholders})
                    ON CONFLICT DO NOTHING
                    RETURNING id
                """),
                {"canonical": canonical_id},
            )
            u = len(result.all())

            # ---- 5. Migrate EventOccurrence -> canonical (skip dupes) ----
            result = await session.execute(
                text(f"""
                    INSERT INTO event_occurrences
                    (event_id, start_date, start_time, end_date, end_time)
                    SELECT
                        :canonical,
                        eo.start_date, eo.start_time,
                        eo.end_date, eo.end_time
                    FROM event_occurrences eo
                    WHERE eo.event_id IN ({dup_placeholders})
                    AND NOT EXISTS (
                        SELECT 1 FROM event_occurrences eo2
                        WHERE eo2.event_id = :canonical
                        AND eo2.start_date = eo.start_date
                        AND COALESCE(eo2.start_time, '')
                            = COALESCE(eo.start_time, '')
                        AND COALESCE(eo2.end_date, '0001-01-01')
                            = COALESCE(eo.end_date, '0001-01-01')
                        AND COALESCE(eo2.end_time, '')
                            = COALESCE(eo.end_time, '')
                    )
                    RETURNING id
                """),
                {"canonical": canonical_id},
            )
            o = len(result.all())

            # ---- 6. Migrate event_tags -> canonical ----
            await session.execute(
                text(f"""
                    INSERT INTO event_tags (event_id, tag_id)
                    SELECT :canonical, et.tag_id
                    FROM event_tags et
                    WHERE et.event_id IN ({dup_placeholders})
                    ON CONFLICT DO NOTHING
                """),
                {"canonical": canonical_id},
            )

            # ---- 7. Clean up duplicates ----
            if not dry_run:
                # Remove orphaned child rows first
                await session.execute(
                    text(f"""
                        DELETE FROM event_occurrences
                        WHERE event_id IN ({dup_placeholders})
                    """)
                )
                await session.execute(
                    text(f"""
                        DELETE FROM event_urls
                        WHERE event_id IN ({dup_placeholders})
                    """)
                )
                await session.execute(
                    text(f"""
                        DELETE FROM event_tags
                        WHERE event_id IN ({dup_placeholders})
                    """)
                )
                # Soft-delete the duplicate events
                await session.execute(
                    text(f"""
                        UPDATE events
                        SET deleted_at = NOW()
                        WHERE id IN ({dup_placeholders})
                    """)
                )

            total_deleted += len(duplicates)
            total_migrated_sources += s
            total_migrated_urls += u
            total_migrated_occurrences += o

            tag = "[DRY-RUN] " if dry_run else ""
            msg = (
                f"{tag}#{canonical_id} '{name[:55]}'"
                f" <- {len(duplicates)} copies"
                f" (+{s}src +{u}url +{o}occ)"
            )
            print(msg)

        if not dry_run:
            await session.commit()
            print(f"\nCommitted. Deleted {total_deleted} duplicates.")
        else:
            print(
                f"\nDRY RUN -- would delete {total_deleted} duplicates"
                f" ({total_migrated_sources} sources,"
                f" {total_migrated_urls} urls,"
                f" {total_migrated_occurrences} occurrences merged)"
            )
            print("Re-run with --apply to commit.")

        result = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE deleted_at IS NULL")
        )
        print(f"Active events after: {result.scalar()}")

        return total_deleted

    finally:
        await session.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(dedup_events(dry_run=dry))
