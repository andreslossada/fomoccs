"""Seed location alternate names to help location resolution dedup.

Usage:  uv run python backend/scripts/seed_location_alternates.py [--apply]

Uses psycopg2 (sync) rather than SQLAlchemy async, because the Supabase
pooler gives asyncpg a ``no tenant identifier`` error when no
``external_id`` or ``snake_hostname`` is provided. Sync access through the
pooler just works with the standard env vars.

Run from the ``backend/`` directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


def _dsn() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )


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
    "Centro Cultural Chacao": [
        "CCCH",
        "Centro Cultural de Chacao",
        "Centro Cultural Chacao - Sala Experimental",
        "Centro Cultural Chacao - Sala Teatro",
    ],
    "Hacienda El Arroyo": ["El Arroyo", "Hacienda Arroyo"],
    "Teatro Alberto de Paz y Mateos": ["Teatro Alberto de Paz"],
    "Universidad Central de Venezuela - Sala Conciertos": [
        "UCV",
        "Ciudad Universitaria",
        "Universidad Central de Venezuela",
        "Aula Magna UCV",
    ],
    "Universidad Católica Andrés Bello (UCAB)": [
        "UCAB",
        "Universidad Catolica Andres Bello",
        "UCAB campus",
    ],
    "Universidad Metropolitana (UNIMET)": ["UNIMET"],
    # Universidad Simón Bolívar stored under USB Sartenejas alias
    "USB Sartenejas": ["USB", "Universidad Simón Bolívar", "Universidad Simon Bolivar"],
}


def seed(dry_run: bool = True) -> None:
    conn = psycopg2.connect(_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    inserted = 0

    for canonical, aliases in KNOWN_ALIASES.items():
        cur.execute(
            "SELECT id FROM locations WHERE name = %s AND deleted_at IS NULL",
            (canonical,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT id FROM locations WHERE LOWER(name) = LOWER(%s) AND deleted_at IS NULL",
                (canonical,),
            )
            row = cur.fetchone()
        if row is None:
            print(f"  SKIP: canonical location '{canonical}' not found")
            continue
        location_id = row[0]

        for alias in aliases:
            cur.execute(
                """
                SELECT 1 FROM location_alternate_names
                WHERE location_id = %s AND LOWER(alternate_name) = LOWER(%s)
                """,
                (location_id, alias),
            )
            if cur.fetchone() is not None:
                continue

            if not dry_run:
                cur.execute(
                    "INSERT INTO location_alternate_names (location_id, alternate_name) VALUES (%s, %s)",
                    (location_id, alias),
                )
            inserted += 1
            print(f"  + [{location_id}] {canonical} <- '{alias}'")

    if not dry_run:
        conn.commit()
        print(f"\nCommitted {inserted} alternate names.")
    else:
        conn.rollback()
        print(f"\nDRY RUN — would insert {inserted} alternate names.")
        print("Run with --apply to commit.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    dry = "--apply" not in sys.argv
    seed(dry_run=dry)
