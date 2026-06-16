"""Seed location alternate names to help location resolution dedup.

Usage:  uv run python backend/scripts/seed_location_alternates.py [--apply]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, text

from api.database import AsyncSessionLocal
from api.models.location import Location, LocationAlternateName

KNOWN_ALIASES: dict[str, list[str]] = {
    "Centro Cultural de Arte Moderno": [
        "CCAM",
        "Centro de Arte Moderno",
        "Centro Cultural de Arte Modern",
        "Antigua Torre centro cultural BOD",
    ],
    "Teatro Teresa Carreño": [
        "TTC",
        "Teatro Teresa Carreno",
        "Sala Ríos Reyna - Teatro Teresa Carreño",
        "Sala Ríos Reyna - TTC",
        "Sala Ríos Reyna",
        "Sala José Félix Ribas - Teatro Teresa Carreño",
        "Sala José Félix Ribas - TTC",
        "Teresa Carreño Theater",
    ],
    "Hotel Eurobuilding": [
        "Salón Turmalina - Hotel Eurobuilding",
        "Hotel Eurobuilding Caracas",
    ],
    "Centro Cultural Chacao": ["CCCH", "Centro Cultural de Chacao"],
    "Hacienda El Arroyo": ["El Arroyo", "Hacienda Arroyo"],
    "Teatro Alberto de Paz y Mateos": ["Teatro Alberto de Paz"],
    "Teatro Chacao": ["Teatro Municipal de Chacao"],
    "Universidad Central de Venezuela": ["UCV", "Ciudad Universitaria"],
    "Universidad Católica Andrés Bello": ["UCAB"],
    "Universidad Metropolitana": ["UNIMET"],
    "Universidad Simón Bolívar": ["USB"],
}


async def seed(dry_run: bool = True) -> None:
    session = AsyncSessionLocal()
    try:
        inserted = 0

        for canonical, aliases in KNOWN_ALIASES.items():
            # Find canonical location (try exact match first, then ILIKE)
            result = await session.execute(
                select(Location).where(
                    Location.name == canonical,
                    Location.deleted_at.is_(None),
                )
            )
            location = result.scalar_one_or_none()
            if location is None:
                # Fallback: case-insensitive match
                result = await session.execute(
                    select(Location).where(
                        Location.name.ilike(canonical),
                        Location.deleted_at.is_(None),
                    )
                )
                location = result.scalar_one_or_none()
            if location is None:
                print(f"  SKIP: canonical location '{canonical}' not found")
                continue

            for alias in aliases:
                existing = await session.scalar(
                    select(text("1"))
                    .where(text("""
                        EXISTS (
                            SELECT 1 FROM location_alternate_names
                            WHERE location_id = :lid AND alternate_name = :name
                        )
                    """))
                    .params(lid=location.id, name=alias)
                )
                if existing:
                    continue

                if not dry_run:
                    alt = LocationAlternateName(
                        location_id=location.id,
                        alternate_name=alias,
                    )
                    session.add(alt)
                inserted += 1
                print(f"  + [{location.id}] {canonical} <- '{alias}'")

        if not dry_run:
            await session.commit()
            print(f"\nCommitted {inserted} alternate names.")
        else:
            print(f"\nDRY RUN — would insert {inserted} alternate names.")
            print("Run with --apply to commit.")
    finally:
        await session.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(seed(dry_run=dry))
