"""Find the event-listing URL for sources that crawl but extract nothing.

A source that points at a venue homepage crawls fine and yields zero
events, because the homepage is not a calendar. This script follows the
homepage's own links and reports which candidates look like an event
listing, ranked by how much event-like text they carry.

It only reports — it never writes to the database.

Usage::

    python scripts/discover_event_urls.py                 # every idle source
    python scripts/discover_event_urls.py --source 82     # one source
"""

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
load_dotenv(os.path.join(_ROOT, "pipeline", ".env"))

# Link text/href fragments that suggest an event listing.
LISTING_HINTS = (
    "agenda",
    "evento",
    "event",
    "calendario",
    "cartelera",
    "programacion",
    "programación",
    "schedule",
    "que-hay",
    "qué-hay",
    "whats-on",
    "actividades",
    "encuentros",
    "conciertos",
    "funciones",
    "espectaculos",
    "espectáculos",
    "ticket",
    "entradas",
    "boletos",
    "agenda-cultural",
    "agenda_cultural",
)

# Words that suggest the page lists dated happenings.
EVENT_TEXT_HINTS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
    "enero",
    "concierto",
    "teatro",
    "exposicion",
    "exposición",
    "museo",
    "taller",
    "charla",
    "jazz",
    "musical",
    "obra",
    "funcion",
    "función",
)

_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class Candidate:
    url: str
    status: int = 0
    text_len: int = 0
    score: int = 0
    hits: list[str] = field(default_factory=list)


def visible_text(html: str) -> str:
    """Strip scripts, styles and tags, leaving readable text."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&[a-z#0-9]{2,8};", " ", html)
    return " ".join(html.split())


def score_text(text: str) -> tuple[int, list[str]]:
    """Score a page by how much it reads like an event listing."""
    lowered = text.lower()
    hits = [m for m in _MONTHS if m in lowered]
    score = len(hits) * 3
    for hint in EVENT_TEXT_HINTS:
        if hint in lowered:
            score += 2
            if len(hits) < 6:
                hits.append(hint)
    return score, hits[:8]


def extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute URLs of links that look like event listings."""
    links: list[str] = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        lowered = href.lower()
        if any(hint in lowered for hint in LISTING_HINTS):
            links.append(href if href.startswith("http") else _join(base_url, href))
    # Keep order, drop duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _join(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)


async def probe(client: httpx.AsyncClient, url: str) -> Candidate | None:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return Candidate(url=url, status=resp.status_code)

    text = visible_text(resp.text)
    score, hits = score_text(text)
    return Candidate(
        url=url, status=200, text_len=len(text), score=score, hits=hits
    )


async def discover(source_id: int, name: str, home_url: str) -> None:
    async with httpx.AsyncClient(
        timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        print(f"\n{'=' * 78}")
        print(f"[{source_id}] {name}")
        print(f"{'=' * 78}")

        try:
            home_resp = await client.get(home_url)
        except httpx.HTTPError as exc:
            print(f"  HOME UNREACHABLE: {home_url} ({type(exc).__name__})")
            return

        home_text = visible_text(home_resp.text)
        home_score, home_hits = score_text(home_text)
        print(
            f"  home: status={home_resp.status_code} text={len(home_text)} "
            f"score={home_score} {home_hits}"
        )
        if home_resp.status_code != 200:
            return

        candidates: list[Candidate] = []
        seen: set[str] = {home_url}
        for link in extract_links(home_resp.text, home_url):
            if link in seen:
                continue
            seen.add(link)
            candidate = await probe(client, link)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: (-c.score, -c.text_len))
        print(f"  {len(candidates)} listing-like link(s) found:")
        for c in candidates[:10]:
            flag = "***" if c.score >= 15 else (" *" if c.score >= 8 else "  ")
            print(
                f"  {flag} score={c.score:<4} text={c.text_len:<7} "
                f"{c.status} {c.url[:70]}"
            )
            if c.hits:
                print(f"        hits: {', '.join(c.hits)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=int, action="append", default=[])
    args = parser.parse_args()

    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )
    cur = conn.cursor()

    if args.source:
        cur.execute(
            """
            SELECT s.id, s.name, su.url
            FROM sources s
            JOIN source_urls su ON su.source_id = s.id AND su.deleted_at IS NULL
            WHERE s.id = ANY(%s)
            ORDER BY s.id
            """,
            (args.source,),
        )
    else:
        cur.execute(
            """
            SELECT s.id, s.name, su.url
            FROM sources s
            JOIN source_urls su ON su.source_id = s.id AND su.deleted_at IS NULL
            WHERE s.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM event_sources es WHERE es.source_id = s.id
              )
            ORDER BY s.id
            """
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    for source_id, name, url in rows:
        await discover(source_id, name, url)


if __name__ == "__main__":
    asyncio.run(main())
