"""
Bulk add new sources, URLs, and locations from the user-provided list.
Only inserts entries that don't already exist in the DB.
"""
import os
import sys

import psycopg2
from psycopg2.extras import execute_values


def _get_db_config():
    """Build DB config from environment variables."""
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    required = ["PROD_DB_HOST", "PROD_DB_NAME", "PROD_DB_USER", "PROD_DB_PASS"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}")
        print("Set DATABASE_URL or PROD_DB_* variables.")
        sys.exit(1)

    return {
        "host": os.getenv("PROD_DB_HOST"),
        "port": int(os.getenv("PROD_DB_PORT", "5432")),
        "dbname": os.getenv("PROD_DB_NAME"),
        "user": os.getenv("PROD_DB_USER"),
        "password": os.getenv("PROD_DB_PASS"),
        "sslmode": "require",
    }

# =============================================================================
# NEW SOURCES with their URLs and tiers
# Tier 1: official venues, cultural centers, universities (every 6h)
# Tier 2: ticketing platforms, info portals (every 12h)
# Tier 3: social media, stealth-required (every 24h)
# =============================================================================

NEW_SOURCES = [
    # --- Ticketing platforms (tier 2) ---
    ("MiTickera", "crawler", 2, "https://mitickera.com"),
    ("Ticketmundo", "crawler", 2, "https://ticketmundo.com.ve"),
    ("Ticketoapp", "crawler", 2, "https://ticketoapp.com"),
    ("Ticketplate", "crawler", 2, "https://ticketplate.com"),
    ("Global Boletos", "crawler", 2, "https://globalboletos.com"),
    ("Kreatickets", "crawler", 2, "https://ventas.kreatickets.com"),
    ("Passline Venezuela", "crawler", 2, "https://www.passline.com/es/ciudad/caracas"),

    # --- Official venues & cultural centers (tier 1) ---
    ("Teatro Teresa Carreno", "crawler", 1, "https://teatroteresacarreno.gob.ve"),
    ("Concha Acustica de Bello Monte", "crawler", 1, "https://alcaldiabaruta.gob.ve"),
    ("Asociacion Cultural Humboldt", "crawler", 1, "https://asociacionculturalhumboldt.com"),
    ("Centro de Arte Los Galpones", "crawler", 1, "https://losgalpones.com"),
    ("Fundacion Rajatabla", "crawler", 1, "https://rajatabla.com.ve"),
    ("Fundarte", "crawler", 1, "https://fundarte.gob.ve"),
    ("Fundacion Museos Nacionales", "crawler", 1, "https://fmn.gob.ve"),
    ("Fundacion Nuevas Bandas", "crawler", 1, "https://nuevasbandas.com"),
    ("Caracas Music Hall", "crawler", 1, "https://caracasmusichall.com"),
    ("Sambil Caracas", "crawler", 1, "https://www.sambil.com.ve/caracas"),
    ("Centro Comercial Lider", "crawler", 1, "https://cclider.com"),
    ("Parque Cerro Verde", "crawler", 1, "https://parquecerroverde.com"),
    ("Tolon Fashion Mall", "crawler", 1, "https://tolon.com.ve"),
    ("Hotel Humboldt", "crawler", 1, "https://hotelhumboldtve.com"),
    ("Hotel Tamanaco Caracas", "crawler", 1, "https://tamanaco.com.ve"),
    ("Hotel Eurobuilding Caracas", "crawler", 1, "https://eurobuilding.com.ve"),
    ("Hacienda La Trinidad Parque Cultural", "crawler", 1, "https://haciendalatrinidad.org"),
    ("CELARG", "crawler", 1, "https://celarg.gob.ve"),
    ("Villa Planchart", "crawler", 1, "https://villapranchart.net"),

    # --- International cultural centers (tier 1) ---
    ("Centro Cultural de Espana en Caracas", "crawler", 1, "https://ccecaracas.org.ve"),
    ("Alianza Francesa de Caracas", "crawler", 1, "https://caracas.afvenezuela.org"),
    ("Goethe-Institut Venezuela", "crawler", 1, "https://www.goethe.de/ins/ve/es/index.html"),
    ("Instituto Italiano de Cultura de Caracas", "crawler", 1, "https://iiccaracas.esteri.it"),

    # --- University cultural agendas (tier 1) ---
    ("Direccion de Cultura UCV", "crawler", 1, "https://cultura.ucv.ve"),
    ("Cultura UCAB", "crawler", 1, "https://cultura.ucab.edu.ve"),
    ("Direccion de Cultura USB", "crawler", 1, "https://cultura.usb.ve"),

    # --- Info portals & agendas (tier 2) ---
    ("TuZonaCaracas", "crawler", 2, "https://tuzonacaracas.com"),
    ("Meetup Caracas", "crawler", 2, "https://www.meetup.com/es/find/ve--caracas/"),
    ("El Estimulo", "crawler", 2, "https://elestimulo.com"),
    ("Runrunes Cultura", "crawler", 2, "https://runrun.es/category/cultura/"),
    ("Cresta Metalica", "crawler", 2, "https://crestametalica.com"),
    ("Ladosis", "crawler", 2, "https://ladosis.com"),
    ("Circuito Gran Cine", "crawler", 2, "https://grancine.net"),
    ("Efecto Cocuyo", "crawler", 2, "https://efectococuyo.com"),
    ("Analitica Entretenimiento", "crawler", 2, "https://analitica.com"),

    # --- Cinema chains (tier 1) ---
    ("Cines Unidos", "crawler", 1, "https://cinesunidos.com"),
    ("Cinex", "crawler", 1, "https://cinex.com.ve"),

    # --- Sports (tier 1) ---
    ("Retos Info", "crawler", 2, "https://retosinfo.com"),
    ("LVBP", "crawler", 1, "https://lvbp.com"),
    ("Liga FUTVE", "crawler", 1, "https://ligafutve.org"),
    ("INH Hipodromo", "crawler", 2, "https://inh.gob.ve"),
]

# =============================================================================
# ADDITIONAL URLs for EXISTING sources
# =============================================================================

EXTRA_URLS = [
    # Source name -> new URL to add
    ("MakeTicket", "https://maketicket.com.ve"),
    ("Centro Cultural Arte Moderno", "https://ccam.org.ve"),
    ("Centro Cultural Chacao", "https://cculturalchacao.com"),
    ("Eventbrite Caracas", "https://www.eventbrite.es/d/venezuela--caracas/events/"),
    ("Contrapunto Cultura", "https://contrapunto.com/categoria/entretenimiento/"),
]

# =============================================================================
# NEW LOCATIONS (venues not yet in DB)
# =============================================================================

NEW_LOCATIONS = [
    ("Hotel Tamanaco Caracas", "venue", "https://tamanaco.com.ve"),
    ("Hotel Humboldt", "venue", "https://hotelhumboldtve.com"),
    ("Sambil Caracas", "venue", "https://www.sambil.com.ve/caracas"),
    ("Parque Cerro Verde", "venue", "https://parquecerroverde.com"),
    ("Tolon Fashion Mall", "venue", "https://tolon.com.ve"),
    ("Teatro Municipal de Caracas", "venue", None),
    ("Teatro Principal de Caracas", "venue", None),
    ("Museo de Ciencias", "venue", None),
    ("Ciudad Universitaria UCV", "venue", None),
    ("USB Sartenejas", "venue", "https://www.usb.ve"),
    ("Estadio Olimpico de la UCV", "venue", None),
    ("Hipodromo La Rinconada", "venue", None),
    ("CELARG Altamira", "venue", "https://celarg.gob.ve"),
    ("Villa Planchart", "venue", "https://villapranchart.net"),
    ("Goethe-Institut Caracas", "venue", "https://www.goethe.de/ins/ve/es/index.html"),
    ("Instituto Italiano de Cultura", "venue", "https://iiccaracas.esteri.it"),
    ("Centro Cultural de Espana Caracas", "venue", "https://ccecaracas.org.ve"),
    ("Fundacion Rajatabla", "venue", "https://rajatabla.com.ve"),
    ("Hacienda La Trinidad", "venue", "https://haciendalatrinidad.org"),
    ("Espacio Caracas (CC Lider)", "venue", "https://cclider.com"),
]


def main():
    config = _get_db_config()
    if isinstance(config, str):
        conn = psycopg2.connect(config)
    else:
        conn = psycopg2.connect(**config)
    cur = conn.cursor()

    # ── Phase 1: Insert new sources ──
    existing_names = set()
    cur.execute("SELECT name FROM sources WHERE deleted_at IS NULL")
    for (n,) in cur.fetchall():
        existing_names.add(n.strip().lower())

    sources_inserted = 0
    urls_inserted = 0
    for name, stype, tier, url in NEW_SOURCES:
        if name.strip().lower() in existing_names:
            print(f"  SKIP source (exists): {name}")
            # Still check if URL needs adding
            cur.execute(
                "SELECT 1 FROM source_urls WHERE url = %s AND deleted_at IS NULL", (url,)
            )
            if not cur.fetchone():
                cur.execute(
                    "SELECT id FROM sources WHERE name = %s AND deleted_at IS NULL", (name,)
                )
                src = cur.fetchone()
                if src:
                    cur.execute(
                        "INSERT INTO source_urls (source_id, url, sort_order) VALUES (%s, %s, 1)",
                        (src[0], url),
                    )
                    urls_inserted += 1
                    print(f"  +URL to existing: {name} -> {url}")
            continue

        cur.execute(
            "INSERT INTO sources (name, type, tier) VALUES (%s, %s, %s) RETURNING id",
            (name, stype, tier),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO source_urls (source_id, url, sort_order) VALUES (%s, %s, 0)",
            (source_id, url),
        )
        sources_inserted += 1
        urls_inserted += 1
        print(f"  +NEW source: {name} (tier={tier}) -> {url}")

    print(f"\nPhase 1: {sources_inserted} new sources, {urls_inserted} new URLs")

    # ── Phase 2: Add extra URLs to existing sources ──
    extra_urls_added = 0
    for source_name, url in EXTRA_URLS:
        cur.execute(
            "SELECT id FROM sources WHERE name = %s AND deleted_at IS NULL", (source_name,)
        )
        src = cur.fetchone()
        if not src:
            print(f"  SKIP extra URL (source not found): {source_name}")
            continue
        cur.execute(
            "SELECT 1 FROM source_urls WHERE url = %s AND deleted_at IS NULL", (url,)
        )
        if cur.fetchone():
            print(f"  SKIP extra URL (already exists): {source_name} -> {url}")
            continue
        cur.execute(
            "INSERT INTO source_urls (source_id, url, sort_order) VALUES (%s, %s, 1)",
            (src[0], url),
        )
        extra_urls_added += 1
        print(f"  +extra URL: {source_name} -> {url}")

    print(f"\nPhase 2: {extra_urls_added} extra URLs added to existing sources")

    # ── Phase 3: Insert new locations ──
    existing_loc_names = set()
    existing_loc_urls = set()
    cur.execute("SELECT LOWER(name), website_url FROM locations WHERE deleted_at IS NULL")
    for n, w in cur.fetchall():
        existing_loc_names.add(n.strip().lower())
        if w:
            existing_loc_urls.add(w.strip().lower())

    locs_inserted = 0
    for name, ltype, website_url in NEW_LOCATIONS:
        name_key = name.strip().lower()
        if name_key in existing_loc_names:
            print(f"  SKIP location (name exists): {name}")
            continue
        if website_url and website_url.strip().lower() in existing_loc_urls:
            print(f"  SKIP location (URL exists): {name} -> {website_url}")
            continue
        cur.execute(
            "INSERT INTO locations (name, type, website_url) VALUES (%s, %s, %s)",
            (name, ltype, website_url),
        )
        locs_inserted += 1
        print(f"  +NEW location: {name}")

    print(f"\nPhase 3: {locs_inserted} new locations")

    conn.commit()
    cur.close()
    conn.close()

    print("\n✅ Done. All changes committed.")


if __name__ == "__main__":
    main()
