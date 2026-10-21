"""Add Instagram venue accounts as pipeline sources.

Venue accounts post their calendars as images and captions, which the
crawler turns into plain text for the extractor. Every caption names the
venue, so an Instagram source adds venues to the map as well as events —
often for venues whose website has no agenda at all.

The candidates below are probed first: an account that is private, renamed
or defunct is skipped rather than added as a source that can never
produce anything.

Usage::

    python scripts/add_instagram_sources.py            # probe only
    python scripts/add_instagram_sources.py --apply    # create the sources
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))

load_dotenv(os.path.join(_ROOT, "pipeline", ".env"), override=False)
load_dotenv(os.path.join(_ROOT, "backend", ".env"), override=False)

# (username, display name, tier)
CANDIDATES: list[tuple[str, str, int]] = [
    ("teatroteresacarreno", "Teatro Teresa Carreño (Instagram)", 1),
    ("ccamoficial", "CCAM (Instagram)", 1),
    ("fundarte", "Fundarte (Instagram)", 1),
    ("culturaucv", "Cultura UCV (Instagram)", 1),
    ("teatrochacao", "Teatro Chacao (Instagram)", 1),
    ("cculturalchacao", "Centro Cultural Chacao (Instagram)", 1),
    ("fundacionrajatabla", "Fundacion Rajatabla (Instagram)", 1),
    ("museobellasaracas", "Museo de Bellas Artes (Instagram)", 1),
    ("museoartecontemporaneo", "Museo de Arte Contemporaneo (Instagram)", 1),
    ("alcaldiadechacao", "Alcaldia de Chacao (Instagram)", 1),
    ("alcaldiaelhatillo", "Alcaldia de El Hatillo (Instagram)", 1),
    ("tuzonacaracas", "TuZonaCaracas (Instagram)", 2),
    ("elgallocinefilo", "El Galerico Cinefilo (Instagram)", 3),
    ("elsistemaorchestra", "El Sistema (Instagram)", 1),
    ("galeriemara", "Galeria Mara (Instagram)", 3),
    ("carmenaraujoarte", "Carmen Araujo Arte (Instagram)", 3),
    ("caracascomiccon", "Caracas Comic Con (Instagram)", 3),
    ("encuentros_ve", "Encuentros Venezuela (Instagram)", 3),
    ("agendad.caracas", "Agenda D Caracas (Instagram)", 3),
    ("quehagoccaracas", "Que Hay Caracas (Instagram)", 3),
]


@dataclass
class Probe:
    username: str
    ok: bool
    posts: int
    detail: str


async def probe(username: str, max_posts: int = 3) -> Probe:
    from instagram import harvest_profile

    cookies_path = os.path.expanduser("~/.config/fomoccs/instagram_cookies.txt")
    try:
        info, posts = await harvest_profile(
            username=username,
            max_posts=max_posts,
            cookies_path=cookies_path if os.path.exists(cookies_path) else None,
        )
    except Exception as exc:  # noqa: BLE001 - a bad handle must not stop the run
        return Probe(username, False, 0, f"{type(exc).__name__}: {exc}"[:80])

    if info.get("is_private"):
        return Probe(username, False, 0, "private account")
    if not posts:
        return Probe(username, False, 0, "no posts harvested")
    return Probe(
        username,
        True,
        len(posts),
        f"{info.get('post_count')} posts, {info.get('full_name')}",
    )


def main(apply: bool) -> None:
    cookies_path = os.path.expanduser("~/.config/fomoccs/instagram_cookies.txt")
    if not os.path.exists(cookies_path):
        print(f"ERROR: no Instagram cookies at {cookies_path}")
        sys.exit(1)

    results: list[Probe] = []
    for username, _name, _tier in CANDIDATES:
        result = asyncio.run(probe(username))
        results.append(result)
        flag = "OK  " if result.ok else "skip"
        print(f"  {flag} @{result.username:<28} {result.detail}")
        # Instagram rate-limits hard; give each account a breather.
        asyncio.run(asyncio.sleep(2))

    working = [r for r in results if r.ok]
    print(f"\n{len(working)}/{len(CANDIDATES)} accounts usable")

    if not apply:
        print("[DRY RUN] Re-run with --apply to create the sources.")
        return

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )
    cur = conn.cursor()

    created = 0
    for result in working:
        username, name, tier = next(
            (u, n, t) for u, n, t in CANDIDATES if u == result.username
        )
        cur.execute(
            "SELECT id FROM sources WHERE name = %s AND deleted_at IS NULL", (name,)
        )
        if cur.fetchone():
            print(f"  SKIP (exists): {name}")
            continue

        cur.execute(
            "INSERT INTO sources (name, type, tier) VALUES (%s, 'crawler', %s) "
            "RETURNING id",
            (name, tier),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO crawl_configs "
            "(source_id, crawl_mode, crawl_frequency, json_api_config) "
            "VALUES (%s, 'instagram', %s, %s)",
            (
                source_id,
                1 if tier == 1 else (2 if tier == 2 else 7),
                psycopg2.extras.Json(
                    {"username": username, "max_posts": 25}
                ),
            ),
        )
        created += 1
        print(f"  +NEW source: {name} (@{username}, tier={tier})")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n[APPLY] Created {created} Instagram sources.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create the sources")
    args = parser.parse_args()
    main(apply=args.apply)
