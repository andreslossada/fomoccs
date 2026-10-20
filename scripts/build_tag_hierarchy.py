"""
Build src/data/tag_hierarchy.json from the tags actually present in the database.

Why this exists
---------------
The pipeline's extractor invents a tag per event, so the `tags` table has
accumulated 600+ raw strings, most of them compound PascalCase Spanish
(`MusicaEnVivo`, `FestivalDeCine`, `ArteAudiovisual`) with near-duplicate
variants (`Musica`/`Música`, `Concierto`/`Conciertos`, `CineFrances`/
`CineFrancés`). The frontend cannot browse that: a popover listing 600 tags
with 30 of them meaning "concert" is worse than no popover.

So this script maps every raw tag onto a small curated taxonomy and emits the
three things the frontend needs:

  tags[]             curated topic nodes (roots + children) that are in use
  tag_redirects      raw tag name -> canonical name, applied client-side when
                     rendering, so `Música` and `Musica` both display as Música
  formats{}          format -> the tags that imply it, used to derive an
                     event's format (Concierto, Taller, Exposición, …) from
                     its tags, since the DB has no event_type column
  format_only_tags   tags that describe a format rather than a topic

Any raw tag that matches nothing stays out of `tags` entirely. The frontend
treats unlisted tags as searchable keywords, which is exactly the right fate
for one-off tag names like `CusicaFest` or `ElsaMorales`.

Re-run after the pipeline learns new tags:
    python scripts/build_tag_hierarchy.py
    python scripts/build_tag_hierarchy.py --dry-run

Requires psycopg2 and python-dotenv (already dependencies of the backend and
pipeline environments); it reads DB credentials from the same .env files they
use, so running it against the deployed database needs no extra setup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "src" / "data" / "tag_hierarchy.json"

# ---------------------------------------------------------------------------
# Taxonomy
#
# (canonical name, emoji, order, quick_filter) for roots; children are
# (canonical name, emoji, aliases). Aliases are matched after normalization, so
# they are written unaccented and unspaced — "musica latina" matches
# "MusicaLatina", "Música Latina" and "musica_latina" alike.
#
# Only roots appear in config/<city>.yaml's filter_roots.tag, which is what the
# tag selector is allowed to head a menu with.
# ---------------------------------------------------------------------------

ROOTS: list[tuple[str, str, int, bool]] = [
    ("Música", "🎵", 1, True),
    ("Teatro", "🎭", 2, True),
    ("Arte", "🎨", 3, True),
    ("Cine", "🎬", 4, True),
    ("Danza", "💃", 5, False),
    ("Literatura", "📚", 6, False),
    ("Comedia", "😂", 7, False),
    ("Cultura", "🏛️", 8, True),
    ("Educación", "🎓", 9, True),
    ("Salud", "🩺", 10, False),
    ("Bienestar", "🧘", 11, False),
    ("Deporte", "⚽", 12, False),
    ("Gastronomía", "🍽️", 13, False),
    ("Familia", "👪", 14, False),
    ("Comunidad", "🤝", 15, True),
    ("Tecnología", "💻", 16, False),
    ("Negocios", "💼", 17, False),
    ("Al aire libre", "🌳", 18, False),
]

CHILDREN: dict[str, list[tuple[str, str, list[str]]]] = {
    "Música": [
        # No bare "clásica"/"clásico" alias here: both exist under Música and
        # Cine, so a bare tag is ambiguous and belongs in the parent compounds
        # ("música clásica", "cine clásico") that are registered automatically.
        ("Clásica", "🎻", ["sinfonico", "orquesta sinfonica", "sinfonica"]),
        ("Latina", "🌴", ["latina", "tropical", "ritmo latino", "ritmos latino"]),
        ("Venezolana", "🇻🇪", ["venezolana", "artista venezolano", "artistas venezolanos", "artista venezolana", "artistas plasticos"]),
        ("Coral", "🎶", ["coral", "canto coral", "coros"]),
        ("Electrónica", "🎛️", ["electronica", "dj", "dj set"]),
        ("Urbana", "🎤", ["urbana", "hip hop", "hiphop", "rap", "reggaeton", "pop urbano", "pop latino", "freestyle"]),
        ("Alternativa", "🎸", ["alternativa", "rock", "indie"]),
        ("Experimental", "🧪", ["experimental", "fusion", "fusion musical"]),
        ("Pop", "🎧", ["pop", "balada pop"]),
        ("Jazz", "🎷", ["jazz"]),
        ("Salsa", "🕺", ["salsa", "mambo", "merengue", "bachata", "boleros", "bolero"]),
        ("Reggae", "🌴", ["reggae"]),
        ("Flamenco", "🔥", ["flamenco"]),
        ("Folklore", "🥁", ["folklore", "folk"]),
        ("Instrumental", "🎻", ["instrumental", "piano", "percussion", "percusion", "camara", "recital de camara"]),
        ("Orquestal", "🎼", ["orquestal", "orquesta", "banda", "banda sinfonica", "sinfonico"]),
    ],
    "Teatro": [
        ("Drama", "🎭", ["drama", "obra teatral", "obra postuma"]),
        ("Musical", "🎶", ["musical"]),
        ("Experimental", "🌀", ["grotowski", "arte scenic", "arte en escena", "artesescenicas", "arteescenico"]),
        ("Formación", "🎓", ["formacion teatral", "investigacion teatral", "catedra teatral", "formacion artistica", "clase magistral"]),
        ("Infantil", "🧸", ["teatro infantil"]),
    ],
    "Arte": [
        ("Exposición", "🖼️", ["exposicion", "muestra de arte", "muestra", "feria", "inauguracion", "coleccion"]),
        ("Artes visuales", "🎨", ["artes visuales", "arte visual", "plasticas", "pintura", "acuarela", "escultura", "ceramica", "collage", "ilustracion", "grabado"]),
        ("Urbano", "🎨", ["arte urbano", "street art", "mural"]),
        ("Digital", "💻", ["arte digital", "arte interactivo", "arte audiovisual"]),
        ("Sonoro", "🎙️", ["arte sonoro", "sonido"]),
        ("Fotografía", "📷", ["fotografia"]),
        ("Creatividad", "✨", ["creatividad", "manualidades", "clases de arte", "arte underground"]),
    ],
    "Cine": [
        ("Clásico", "🎞️", []),
        ("Francés", "🥖", ["cultura francesa"]),
        ("Independiente", "🎥", ["cine venezolano", "documental", "cortometraje", "peliculas"]),
        ("Ciclo", "🎞️", ["ciclo de camara", "ciclos"]),
        ("Proyección", "📽️", ["proyeccion", "cinemateca nacional"]),
    ],
    "Danza": [
        ("Ballet", "🩰", ["ballet"]),
        ("Contemporánea", "🩰", ["contemporanea", "danza contemporanea"]),
        ("Tradicional", "🕺", ["folclorica", "temporada danza"]),
    ],
    "Literatura": [
        ("Lectura", "📖", ["lectura", "firma de libros", "presentacion de libros", "librerias"]),
        ("Escritura", "✍️", ["escritura", "cuento", "poesia", "narrativa"]),
    ],
    "Comedia": [
        ("Stand-up", "🎙️", ["stand up", "standup", "stand up comedy", "monologo"]),
    ],
    "Cultura": [
        ("Patrimonio", "🏛️", ["cocina patrimonial", "ruta cultural", "patrimonio"]),
        ("Historia", "📜", ["historia", "memoria"]),
        ("Identidad", "🇻🇪", ["ciudadania", "conciencia social", "empoderamiento"]),
        ("Fútbol", "⚽", ["futbol", "mundial", "gira mundial", "mundial 2026"]),
    ],
    "Educación": [
        ("Cursos", "📚", ["curso", "cursos", "formacion", "taller", "talleres", "clase", "clases", "capacitacion", "inscripciones", "certificacion", "diplomado"]),
        # A conference is a format, not a topic — it lives in FORMATS only, so
        # it never shows up as a chip under Educación.
        ("Investigación", "🔬", ["investigacion", "analisis", "conversatorio"]),
        ("Idiomas", "🗣️", ["idiomas", "idioma italiano", "certificacion idiomas"]),
    ],
    "Salud": [
        ("Salud pública", "🏥", ["salud publica", "medicina"]),
    ],
    "Bienestar": [
        ("Yoga", "🧘", ["yoga", "clases de yoga"]),
        ("Meditación", "🕉️", ["meditacion"]),
        ("Ejercicio", "🏃", ["ejercicio", "fitness", "gimnasio", "entrenamiento"]),
    ],
    "Deporte": [
        ("Competencia", "🏆", ["competencia", "torneo", "campeonato", "automovilismo"]),
    ],
    "Gastronomía": [
        ("Cocina", "🍳", ["cocina", "gastronomica", "cocina dc", "sabores", "dulces", "postres", "restaurante"]),
        ("Café", "☕", ["cafe", "cafeteria", "panaderia"]),
    ],
    "Familia": [
        ("Infantil", "🧸", ["infantil", "ninos", "ninas", "entretenimiento familiar", "familia"]),
    ],
    "Comunidad": [
        ("Encuentros", "🤝", ["encuentro", "convocatoria", "asociacion cultural", "conferenciaCBC"]),
        ("Voluntariado", "💪", ["voluntariado", "donacion", "ayuda", "solidaridad"]),
    ],
    "Tecnología": [
        ("Innovación", "🚀", ["innovacion", "inteligencia artificial", "ia", "programacion", "digitalizacion"]),
    ],
    "Negocios": [
        ("Networking", "🤝", ["networking", "networking de negocios"]),
        ("Economía", "💹", ["economia", "finanzas", "negocios", "corporativo", "emprendimiento"]),
    ],
    "Al aire libre": [
        ("Naturaleza", "🌿", ["aire libre", "aireLibre", "naturaleza", "ecologia", "senderismo"]),
    ],
}

# Formats describe *what kind of event* this is, orthogonal to the topic. The
# frontend derives an event's format from its tags because there is no
# event_type column: the first matching tag wins.
FORMATS: dict[str, list[str]] = {
    "Concierto": ["concierto", "conciertos", "concierto benefico", "concierto en vivo", "show musical", "espectaculo musical", "show", "show en vivo", "recital", "gala musical"],
    "Festival": ["festival", "festival de musica", "festival de cine", "festival cine", "carnaval", "parada"],
    "Obra de teatro": ["teatro", "obra teatral", "musical", "monologo", "obra postuma", "funcion"],
    "Comedia": ["comedia", "stand up", "standup", "stand up comedy", "humor", "chiste"],
    "Cine": ["cine", "proyeccion", "cine frances", "festival de cine", "festival cine", "ciclo de cine", "ciclo", "cortometraje", "documental"],
    "Exposición": ["exposicion", "muestra de arte", "muestra", "feria", "inauguracion", "galeria de arte", "salon de arte"],
    "Taller": ["taller", "talleres", "manualidades", "clase magistral"],
    "Curso": ["curso", "cursos", "clase", "clases", "formacion", "capacitacion", "inscripciones"],
    "Conferencia": ["conferencia", "charla", "charlas", "conversatorio", "foro", "panel", "catedra", "clase magistral"],
    "Deporte": ["competencia", "torneo", "campeonato", "partido", "futbol", "mundial", "gira mundial"],
    "Empresa": ["networking", "networking de negocios", "corporativo", "negocios", "economia", "emprendimiento"],
    "Convocatoria": ["convocatoria", "inscripciones", "becas", "concurso", "premio", "premios", "certificacion", "becas de investigacion"],
    "Beneficio": ["concierto benefico", "donacion", "solidaridad"],
    "Participativo": ["taller", "talleres", "curso", "cursos", "clase", "clases", "capacitacion", "conversatorio", "manualidades"],
    "Presentacion": ["presentacion", "presentacion de libros", "firma de libros", "lanzamiento", "inauguracion", "estreno", "estrenos"],
}

# Which experience family each format belongs to. The frontend uses these as
# the colour key for map discs and dot-density tints, and they must match the
# keys in src/js/core/formatColors.js — an unknown category silently degrades
# every marker in it to the neutral "Other" grey.
#   Performance  you watch/listen
#   Participatory you take part
#   Outing       you go somewhere / do business
FORMAT_CATEGORIES: dict[str, list[str]] = {
    "Performance": [
        "Concierto", "Festival", "Obra de teatro", "Comedia", "Cine",
        "Exposición", "Presentacion", "Beneficio",
    ],
    "Participatory": ["Taller", "Curso", "Conferencia", "Convocatoria", "Deporte"],
    "Outing": ["Empresa", "Participativo"],
}


# ---------------------------------------------------------------------------
# Normalization and matching
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Fold a tag to its comparison form: lowercase, unaccented, alphanumeric.

    Collapses every spelling gap the extractor produces — accents, spaces,
    underscores, hyphens, case — so `Música En Vivo`, `musica_en_vivo` and
    `MUSICAENVIVO` all reduce to `musicaenvivo`.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def tokenize(text: str) -> list[str]:
    """Split a tag into lowercase words on separators *and* camelCase humps.

    The extractor emits PascalCase compounds, so `NochesDeGuataca` has to read
    as three words. Without the camelCase split, "cafe" matches inside
    `CusicaFest` and the festival is filed under cafés.
    """
    spaced = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", text)
    spaced = re.sub(r"(?<=[A-ZÁÉÍÓÚÑ])(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ])", " ", spaced)
    words = [w for w in re.split(r"[^0-9A-Za-zÁÉÍÓÚÑáéíóúñ]+", spaced) if w]
    return [normalize(w) for w in words]


def _ngrams(tokens: list[str], max_n: int = 4) -> list[tuple[int, tuple[str, ...]]]:
    """Contiguous token windows, longest first.

    Order matters: `MusicaLatina` must be tried as the two-word sequence
    "musica latina" before the bare word "musica", otherwise every compound
    collapses onto its first word.
    """
    out = []
    for size in range(min(max_n, len(tokens)), 0, -1):
        for i in range(len(tokens) - size + 1):
            out.append((size, tuple(tokens[i : i + size])))
    return out


def build_vocabulary() -> dict[str, tuple[str, str | None, str]]:
    """normalized key -> (canonical name, parent or None, emoji).

    Alongside each child's own name, register its `parent+child` and
    `child+parent` compounds. The extractor's habit of repeating the parent
    inside the child (`MusicaLatina`, `CineFrances`, `ArteUrbano`) is the most
    common shape in the table, and without those keys every compound would
    resolve to its parent root alone.
    """
    vocab: dict[str, tuple[str, str | None, str]] = {}
    for name, emoji, _order, _qf in ROOTS:
        vocab[normalize(name)] = (name, None, emoji)
    for parent, children in CHILDREN.items():
        pkey = normalize(parent)
        for name, emoji, aliases in children:
            key = normalize(name)
            entry = (name, parent, emoji)
            vocab[key] = entry
            if pkey and key:
                vocab.setdefault(pkey + key, entry)
                vocab.setdefault(key + pkey, entry)
            for alias in aliases:
                vocab.setdefault(normalize(alias), entry)
    return vocab


def _lookup(vocab: dict[str, tuple[str, str | None, str]], phrase: tuple[str, ...]) -> tuple[str, str | None, str] | None:
    return vocab.get("".join(phrase))


def match_tag(raw: str, vocab: dict[str, tuple[str, str | None, str]]) -> tuple[str, str | None, str] | None:
    """Resolve one raw tag to (canonical, parent, emoji), or None if uncurated.

    Whole-token matching only: a bare-word alias must appear as its own word,
    never as a run of letters inside an unrelated word. That is what keeps
    `CusicaFest` (a festival) out of `Café`.
    """
    tokens = tokenize(raw)
    if not tokens:
        return None

    for _size, phrase in _ngrams(tokens):
        hit = _lookup(vocab, phrase)
        if hit:
            return hit

    # A root word anywhere in the tag is still a signal: `TeatroTeresaCarreño`
    # already matches "teatro", but `JazzEnElParque` would only match "jazz",
    # so keep single-word scanning across the whole tag before giving up.
    best: tuple[str, str | None, str] | None = None
    for token in tokens:
        hit = _lookup(vocab, (token,))
        if hit and best is None:
            best = hit
    return best


def match_format(raw: str) -> str | None:
    """Resolve one raw tag to a format name, or None.

    Same whole-token rule as `match_tag`, over the format vocabulary.
    """
    tokens = tokenize(raw)
    if not tokens:
        return None
    best: tuple[int, str] | None = None
    for fmt, aliases in FORMATS.items():
        keys = {normalize(fmt)} | {normalize(a) for a in aliases}
        for size, phrase in _ngrams(tokens):
            if "".join(phrase) in keys and (best is None or size > best[0]):
                best = (size, fmt)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def _get_db_config():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    if all(os.getenv(v) for v in ("PROD_DB_HOST", "PROD_DB_NAME", "PROD_DB_USER", "PROD_DB_PASS")):
        return {
            "host": os.getenv("PROD_DB_HOST"),
            "port": int(os.getenv("PROD_DB_PORT", "5432")),
            "dbname": os.getenv("PROD_DB_NAME"),
            "user": os.getenv("PROD_DB_USER"),
            "password": os.getenv("PROD_DB_PASS"),
            "sslmode": "require",
        }

    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}")
        print("Set DATABASE_URL, PROD_DB_*, or DB_* variables (e.g. via .env).")
        sys.exit(1)

    return {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASS"),
        "sslmode": os.getenv("DB_SSLMODE", "require"),
    }


def fetch_tag_counts() -> dict[str, int]:
    """tag name -> number of events carrying it."""
    config = _get_db_config()
    # A DATABASE_URL is a DSN string; the PROD_DB_*/DB_* branches build a
    # keyword dict, which psycopg2 only accepts splatted.
    conn = psycopg2.connect(config) if isinstance(config, str) else psycopg2.connect(**config)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.name, COUNT(et.event_id)
                FROM tags t
                LEFT JOIN event_tags et ON et.tag_id = t.id
                GROUP BY t.name
                """
            )
            return {name: count for name, count in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build(tag_counts: dict[str, int]) -> dict:
    vocab = build_vocabulary()

    # Which curated nodes are actually in use, and under which raw spellings.
    node_hits: dict[str, dict] = {}
    redirects: dict[str, str] = {}
    formats: dict[str, list[str]] = {}

    for raw in sorted(tag_counts, key=lambda k: normalize(k)):
        hit = match_tag(raw, vocab)
        canonical = hit[0] if hit else raw

        if hit:
            node = node_hits.setdefault(
                canonical,
                {"name": canonical, "parents": [], "use_count": 0, "aliases": set()},
            )
            node["use_count"] += tag_counts[raw]
            if hit[1] and hit[1] not in node["parents"]:
                node["parents"] = [hit[1]]
            if normalize(raw) != normalize(canonical):
                node["aliases"].add(raw)
            redirects[raw] = canonical

        fmt = match_format(raw)
        if fmt:
            formats.setdefault(fmt, [])
            if canonical not in formats[fmt]:
                formats[fmt].append(canonical)

    root_names = {name for name, _e, _o, _q in ROOTS}
    child_of: dict[str, str] = {
        name: parent for parent, children in CHILDREN.items() for name, _e, _a in children
    }
    emoji_of = {name: emoji for name, emoji, _o, _q in ROOTS}
    for children in CHILDREN.values():
        for name, emoji, _aliases in children:
            emoji_of[name] = emoji
    order_of = {name: order for name, _e, order, _q in ROOTS}

    # A child with no emitted root would be unreachable: the frontend builds
    # its parent maps from `tags[]`, so an orphaned child silently disappears
    # from the menu. Emit a root when it was matched directly *or* when any of
    # its children were.
    used_children = [n for n in node_hits if n in child_of]
    needed_roots = {child_of[n] for n in used_children}
    used_roots = [r for r in ROOTS if r[0] in node_hits or r[0] in needed_roots or not node_hits]

    tags = []
    for name, _e, order, quick_filter in used_roots:
        hits = node_hits.get(name, {})
        tags.append(
            {
                "name": name,
                "parents": [],
                "emoji": emoji_of[name],
                "quickFilter": quick_filter,
                "order": order,
                **({"aliases": sorted(hits["aliases"])} if hits.get("aliases") else {}),
            }
        )
    child_order = sorted(used_children, key=lambda n: (child_of[n], order_of.get(n, 999), n))
    for name in child_order:
        hits = node_hits[name]
        tags.append(
            {
                "name": name,
                "parents": [child_of[name]],
                "emoji": emoji_of.get(name, ""),
                "aliases": sorted(hits["aliases"]),
            }
        )

    # A tag is "format only" when nothing but the format vocabulary claims it.
    # Roots keep their topic role even if they double as a format word
    # (`Cultura` is a topic; `Concierto` is not).
    topic_names = {t["name"] for t in tags}
    format_only = sorted(
        {name for names in formats.values() for name in names} - topic_names
    )
    emitted = set(formats)

    return {
        "tags": tags,
        # Two different shapes, two different consumers:
        #   formats      category -> [format]  — the selector's menus and the
        #                marker/dot colour key (formatColors.js reads this).
        #   format_tags  format -> [tag]      — how the frontend derives an
        #                event's format, since the DB has no event_type column.
        "formats": {
            category: sorted(name for name in names if name in emitted)
            for category, names in FORMAT_CATEGORIES.items()
        },
        "format_tags": {fmt: sorted(names) for fmt, names in sorted(formats.items())},
        "format_only_tags": format_only,
        "tag_redirects": dict(sorted(redirects.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output path (default: src/data/tag_hierarchy.json)")
    parser.add_argument("--dry-run", action="store_true", help="print a summary without writing")
    args = parser.parse_args()

    for env_path in (ROOT / ".env", ROOT / "backend" / ".env", ROOT / "pipeline" / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)

    tag_counts = fetch_tag_counts()
    result = build(tag_counts)

    total_raw = len(tag_counts)
    curated = len({normalize(t) for t in result["tag_redirects"]})
    stats = [
        f"raw tags in DB:        {total_raw}",
        f"mapped to taxonomy:    {curated}",
        f"left as keywords:      {total_raw - curated}",
        f"curated nodes emitted: {len(result['tags'])}",
        f"formats emitted:       {len(result['formats'])}",
    ]

    if args.dry_run:
        print("\n".join(stats))
        print("\nroots:")
        for tag in result["tags"]:
            if not tag["parents"]:
                kids = [t["name"] for t in result["tags"] if tag["name"] in t["parents"]]
                print(f"  {tag['name']:<16} {len(kids):>2} children  {' '.join(kids[:8])}")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n".join(stats))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()