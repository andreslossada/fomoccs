"""Assign emoji and alt_emoji to locations based on name patterns.

Usage:  uv run python backend/scripts/assign_location_emojis.py [--apply]
"""

from __future__ import annotations

import asyncio
import re
import sys

from sqlalchemy import text

from api.database import AsyncSessionLocal

EMOJI_RULES: list[tuple[str, str, str | None]] = [
    # (pattern, emoji, alt_emoji)
    (r"\bteatro\b", "\U0001f3ad", "\U0001f3ac"),            # performing arts, cinema
    (r"\btheater\b", "\U0001f3ad", "\U0001f3ac"),
    (r"\bmuseo\b", "\U0001f3db\ufe0f", "\U0001f3a8"),       # museum, art
    (r"\bmuseum\b", "\U0001f3db\ufe0f", "\U0001f3a8"),
    (r"\bgaler[ií]a\b", "\U0001f3a8", "\U0001f5bc\ufe0f"),  # art, framed picture
    (r"\bgallery\b", "\U0001f3a8", "\U0001f5bc\ufe0f"),
    (r"\bcentro cultural\b", "\U0001f3e2", "\U0001f3ad"),   # building, performing arts
    (r"\bcultural centre\b", "\U0001f3e2", "\U0001f3ad"),
    (r"\bsala(\s+de)?\s+concierto", "\U0001f3b5", "\U0001f3ba"),  # music, violin
    (r"\bconcierto\b", "\U0001f3b5", "\U0001f3ba"),
    (r"\baudit[oó]ri[ou]m?\b", "\U0001f3a4", "\U0001f3ad"),  # mic, performing arts
    (r"\baula magna\b", "\U0001f3a4", "\U0001f3ad"),
    (r"\bcine(ma)?\b", "\U0001f3ac", "\U0001f37f"),          # cinema, popcorn
    (r"\bcinemateca\b", "\U0001f3ac", "\U0001f37f"),
    (r"\bpoliedro\b", "\U0001f3df\ufe0f", "\U0001f3c6"),     # stadium, trophy
    (r"\bestadio\b", "\U0001f3df\ufe0f", "\U0001f3c6"),
    (r"\bgimnasio\b", "\U0001f3cb\ufe0f", "\U0001f3c6"),     # weightlifting, trophy
    (r"\bgym\b", "\U0001f3cb\ufe0f", "\U0001f3c6"),
    (r"\bbar\b", "\U0001f37a", "\U0001f3b5"),                 # beer, music
    (r"\bclub\b", "\U0001f3b6", "\U0001f37a"),                # notes, beer
    (r"\bdisco\b", "\U0001f3b6", "\U0001f3aa"),               # notes, disco ball
    (r"\biglesia\b", "\u26ea", "\U0001f54d"),                  # church, menorah
    (r"\bbas[ií]lica\b", "\u26ea", "\U0001f54d"),
    (r"\bchurch\b", "\u26ea", "\U0001f54d"),
    (r"\buniversidad\b", "\U0001f393", "\U0001f4da"),        # graduation cap, books
    (r"\buniversity\b", "\U0001f393", "\U0001f4da"),
    (r"\bucv\b", "\U0001f393", "\U0001f4da"),
    (r"\bucab\b", "\U0001f393", "\U0001f4da"),
    (r"\bunimet\b", "\U0001f393", "\U0001f4da"),
    (r"\bhotel\b", "\U0001f3e8", "\U0001f6d6"),              # hotel, hotel-bell
    (r"\bhospedaje\b", "\U0001f3e8", "\U0001f6d6"),
    (r"\bhostel\b", "\U0001f3e8", "\U0001f6d6"),
    (r"\bparque\b", "\U0001f333", "\U0001f3de\ufe0f"),       # tree, national park
    (r"\bpark\b", "\U0001f333", "\U0001f3de\ufe0f"),
    (r"\bplaza\b", "\U0001f3d7\ufe0f", "\U0001f333"),  # construction, tree
    (r"\bboulevard\b", "\U0001f3d7\ufe0f", "\U0001f333"),
    (r"\bjard[ií]n\b", "\U0001f33a", "\U0001f333"),           # hibiscus, tree
    (r"\bgarden\b", "\U0001f33a", "\U0001f333"),
    (r"\brestaurant\b", "\U0001f37d\ufe0f", "\U0001f374"),   # plate, fork+knife
    (r"\bfood\b", "\U0001f37d\ufe0f", "\U0001f374"),
    (r"\bgastron[oó]m[io]", "\U0001f37d\ufe0f", "\U0001f374"),
    (r"\bbiblioteca\b", "\U0001f4da", "\U0001f3db\ufe0f"),   # books, museum
    (r"\blibrary\b", "\U0001f4da", "\U0001f3db\ufe0f"),
    (r"\blibrer[ií]a\b", "\U0001f4da", "\U0001f4d6"),        # books, open book
    (r"\btienda\b", "\U0001f3ea", "\U0001f4cd"),              # shop, pin
    (r"\bshop\b", "\U0001f3ea", "\U0001f4cd"),
    (r"\bstore\b", "\U0001f3ea", "\U0001f4cd"),
    (r"\bcentro comercial\b", "\U0001f3ec", "\U0001f6d2"),   # department store, cart
    (r"\bshopping\b", "\U0001f3ec", "\U0001f6d2"),
    (r"\bmall\b", "\U0001f3ec", "\U0001f6d2"),
    (r"\bccct\b", "\U0001f3ec", "\U0001f6d2"),
    (r"\bembajada\b", "\U0001f3f0", "\U0001f1fb\U0001f1ea"),  # castle, VE flag
    (r"\bembassy\b", "\U0001f3f0", ""),
    (r"\bfundaci[oó]n\b", "\U0001f3e6", "\U0001f48e"),       # office building, gem
    (r"\bfoundation\b", "\U0001f3e6", "\U0001f48e"),
    (r"\bestudio\b", "\U0001f3ac", "\U0001f3a5"),             # cinema, camera
    (r"\btalleres?\b", "\U0001f528", "\U0001f5a5\ufe0f"),     # hammer+wrench, desktop
    (r"\bworkshop\b", "\U0001f528", "\U0001f5a5\ufe0f"),
    (r"\bvarias?\s+sedes?\b", "\U0001f30d", "\U0001f4cd"),    # globe, pin
    (r"\bm[uú]ltiples\s+sedes?\b", "\U0001f30d", "\U0001f4cd"),
    (r"\bdiversas?\s+salas?\b", "\U0001f30d", "\U0001f4cd"),
    (r"\bonline\b", "\U0001f4bb", "\U0001f310"),              # laptop, globe
    (r"\bstreaming\b", "\U0001f4fa", "\U0001f310"),           # TV, globe
    (r"\baire libre\b", "\U0001f30c", "\U0001f3de\ufe0f"),    # milky way, national park
]

DEFAULT_EMOJI = "\U0001f4cd"  # pushpin
DEFAULT_ALT = "\U0001f4cc"    # round pushpin


async def assign_emojis(dry_run: bool = True) -> int:
    session = AsyncSessionLocal()
    try:
        result = await session.execute(
            text(
                "SELECT id, name, lower(name) as lname "
                "FROM locations WHERE deleted_at IS NULL"
            )
        )
        locations = list(result.mappings().all())

        assigned = 0
        for loc in locations:
            lname: str = loc["lname"]
            matched = False
            for pattern, emoji, alt in EMOJI_RULES:
                if re.search(pattern, lname, re.IGNORECASE):
                    if not dry_run:
                        await session.execute(
                            text(
                                "UPDATE locations SET emoji = :e, alt_emoji = :a "
                                "WHERE id = :lid"
                            ),
                            {"e": emoji, "a": alt, "lid": loc["id"]},
                        )
                    else:
                        pass
                    assigned += 1
                    matched = True
                    break

            if not matched:
                if not dry_run:
                    await session.execute(
                        text(
                            "UPDATE locations SET emoji = :e, alt_emoji = :a "
                            "WHERE id = :lid AND emoji IS NULL"
                        ),
                        {"e": DEFAULT_EMOJI, "a": DEFAULT_ALT, "lid": loc["id"]},
                    )
                assigned += 1

        if not dry_run:
            await session.commit()

        tag = "[DRY-RUN] " if dry_run else ""
        print(f"{tag}Assigned emojis to {assigned}/{len(locations)} locations")

        if dry_run:
            print("Re-run with --apply to commit.")

        return assigned
    finally:
        await session.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    asyncio.run(assign_emojis(dry_run=dry))
