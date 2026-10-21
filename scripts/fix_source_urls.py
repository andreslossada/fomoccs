"""Repair the source table: better URLs, stealth for blocked sites, retire dead ones.

Three classes of problem were found by auditing the sources that crawl
successfully but yield zero events:

* **Wrong URL** — the source points at a venue homepage instead of its
  calendar, so the crawl succeeds and extracts nothing.
* **Blocked** — the site answers 403/406 to a plain request. The crawler's
  stealth mode gets past that.
* **Dead** — the domain no longer resolves. Retiring the source stops it
  from burning a crawl slot on every run.

Usage::

    python scripts/fix_source_urls.py            # dry run
    python scripts/fix_source_urls.py --apply    # commit
"""

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg2
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
load_dotenv(os.path.join(_ROOT, "pipeline", ".env"), override=False)
load_dotenv(os.path.join(_ROOT, "backend", ".env"), override=False)


@dataclass
class Fix:
    source: str
    urls: list[str]
    stealth: bool = False
    retire: bool = False
    note: str = ""


# Sources whose calendar lives on a different page than the one we had.
URL_FIXES: list[Fix] = [
    Fix(
        "El Sistema",
        ["https://elsistema.org.ve/calendario/"],
        note="calendar page, not the homepage",
    ),
    Fix(
        "Alianza Francesa de Caracas",
        ["https://afcaracas.com/eventos/"],
        note="agenda page moved to afcaracas.com",
    ),
    Fix(
        "MiTickera",
        ["https://www.mitickera.com/eventos/"],
        note="event index, homepage only lists the nav",
    ),
    Fix(
        "Runrunes Cultura",
        ["https://runrun.es/tag/cultura/"],
        note="/category/cultura/ is a 404; the tag archive works",
    ),
    Fix(
        "Carmen Araujo Arte",
        ["https://carmenaraujoarte.com/exhibiciones/"],
        note="exhibition index",
    ),
    Fix(
        "GBG Arts",
        ["https://gbgarts.com/exhibiciones/"],
        note="exhibition index",
    ),
    Fix(
        "OMM Produccion Cartelera",
        ["https://ommproduccion.jimdofree.com/cartelera-de-teatro/"],
        note="theatre cartelera index",
    ),
    Fix(
        "Alcaldia de Chacao",
        [],
        retire=True,
        note="alcaldiadechacao.gob.ve does not resolve",
    ),
    Fix(
        "Alcaldia de El Hatillo",
        [],
        retire=True,
        note="alcaldiaelhatillo.gob.ve does not resolve",
    ),
    Fix(
        "CELARG",
        [],
        retire=True,
        note="celarg.gob.ve does not resolve",
    ),
    Fix(
        "Centro Cultural de Espana en Caracas",
        [],
        retire=True,
        note="ccecaracas.org.ve does not resolve",
    ),
    Fix(
        "Checkea.net Eventos Caracas",
        [],
        retire=True,
        note="checkea.net does not resolve",
    ),
    Fix(
        "Cinematografica Blancica",
        [],
        retire=True,
        note="blancica.com does not resolve",
    ),
    Fix(
        "Direccion de Cultura USB",
        [],
        retire=True,
        note="cultura.usb.ve does not resolve",
    ),
    Fix(
        "FLOC Feria del Libro Oeste",
        [],
        retire=True,
        note="floc.ucab.edu.ve does not resolve",
    ),
    Fix(
        "Fundacion Museos Nacionales",
        [],
        retire=True,
        note="fmn.gob.ve does not resolve",
    ),
    Fix(
        "Fundacion Nuevas Bandas",
        [],
        retire=True,
        note="nuevasbandas.com does not resolve",
    ),
    Fix(
        "Fundacion Rajatabla",
        [],
        retire=True,
        note="rajatabla.com.ve does not resolve",
    ),
    Fix(
        "Hotel Humboldt",
        [],
        retire=True,
        note="hotelhumboldtve.com does not resolve",
    ),
    Fix(
        "Hotel Tamanaco Caracas",
        [],
        retire=True,
        note="tamanaco.com.ve does not resolve",
    ),
    Fix(
        "Ladosis",
        [],
        retire=True,
        note="ladosis.com does not resolve",
    ),
    Fix(
        "Sambil Caracas",
        [],
        retire=True,
        note="sambil.com.ve does not resolve",
    ),
    Fix(
        "Tolon Fashion Mall",
        [],
        retire=True,
        note="tolon.com.ve does not resolve",
    ),
    Fix(
        "Uneticket",
        [],
        retire=True,
        note="uneticket.com.ve does not resolve",
    ),
    Fix(
        "Villa Planchart",
        [],
        retire=True,
        note="villapranchart.net does not resolve",
    ),
    Fix(
        "Cines Unidos",
        [],
        retire=True,
        note="cinesunidos.com no longer responds",
    ),
    Fix(
        "Teatro Teresa Carreno",
        [],
        retire=True,
        note="teatroteresacarreno.gob.ve returns an empty page",
    ),
]

# Sites that answer 403/406 to a plain request — the crawler's stealth
# mode is what gets through.
STEALTH_FIXES: list[str] = [
    "Songkick Caracas",
    "Trasnocho Cultural",
    "Passline Venezuela",
    "Caracas Music Hall",
    "El Cooperante Entretenimiento",
    "INH Hipodromo",
    "Efecto Cocuyo",
    "La Patilla Entretenimiento",
    "Cronica Uno Cultura",
    "Caracas Comic Con",
]


def main(apply: bool) -> None:
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )
    cur = conn.cursor()

    urls_added = 0
    retired = 0
    stealthed = 0

    for fix in URL_FIXES:
        cur.execute(
            "SELECT id FROM sources WHERE name = %s AND deleted_at IS NULL", (fix.source,)
        )
        row = cur.fetchone()
        if not row:
            print(f"  SKIP (source not found): {fix.source}")
            continue
        source_id = row[0]

        if fix.retire:
            cur.execute(
                "UPDATE sources SET disabled = TRUE WHERE id = %s", (source_id,)
            )
            retired += 1
            print(f"  RETIRE {fix.source}: {fix.note}")
            continue

        for url in fix.urls:
            cur.execute(
                "SELECT id FROM source_urls WHERE source_id = %s AND url = %s "
                "AND deleted_at IS NULL",
                (source_id, url),
            )
            if cur.fetchone():
                print(f"  SKIP url (exists): {fix.source} -> {url}")
                continue
            cur.execute(
                "INSERT INTO source_urls (source_id, url, sort_order) VALUES (%s, %s, 0)",
                (source_id, url),
            )
            urls_added += 1
            print(f"  +URL  {fix.source} -> {url}  ({fix.note})")

    for name in STEALTH_FIXES:
        cur.execute(
            "SELECT s.id FROM sources s JOIN crawl_configs cc ON cc.source_id = s.id "
            "WHERE s.name = %s AND s.deleted_at IS NULL",
            (name,),
        )
        row = cur.fetchone()
        if not row:
            print(f"  SKIP (source not found): {name}")
            continue
        cur.execute(
            "UPDATE crawl_configs SET use_stealth = TRUE WHERE source_id = %s", (row[0],)
        )
        stealthed += 1
        print(f"  STEALTH {name}")

    print()
    print(f"urls_added={urls_added} retired={retired} stealthed={stealthed}")

    if not apply:
        print("[DRY RUN] Re-run with --apply to commit.")
        conn.rollback()
        return

    conn.commit()
    cur.close()
    conn.close()
    print("[APPLY] Committed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the changes")
    args = parser.parse_args()
    main(apply=args.apply)
