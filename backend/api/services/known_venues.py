"""Curated coordinates for Caracas venues whose external geocoding often fails.

When the Google Places / Geoapify geocoder can't resolve a venue (it returns
NULL lat/lng), the event becomes invisible on the map. ``KNOWN_VENUES`` is a
final fallback so we never lose events to bad geocoding.

Each entry is keyed by an exact match string (canonical name or alias). When
:func:`backend.api.services.event_processing.resolve_location` receives a
location whose normalized name matches one of these keys, it short-circuits
geocoding and pins the event to the curated coordinates.

Coordinates are sourced from OpenStreetMap/Google Maps and verified manually.
If a coordinate is wrong, fix it here and re-run the geocoding backfill.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueCoords:
    lat: float
    lng: float
    address: str
    emoji: str = "\U0001f3ad"  # 🎭


def _aliases(coords: VenueCoords, *names: str) -> dict[str, VenueCoords]:
    """Map every alias in *names* to the same curated coordinates.

    Venues are commonly referred to by several names that all normalize to
    different lookup keys ("ciudad universitaria ucv" and "ciudad
    universitaria", for example). Grouping them keeps the shared coordinate
    in one place instead of repeating the literal — and avoids repeating a
    dict key, which Python silently collapses to the last value.
    """
    return dict.fromkeys(names, coords)


# Canonical names match exactly against the venue name after
# :func:`_normalize` (NFKD + lower + strip combining diacritics + strip
# punctuation + collapse whitespace). Aliases are an OR — first match wins.
_KNOWN_VENUES: dict[str, VenueCoords] = {
    # ----- Theaters -----
    "teatro teresa carreno": VenueCoords(
        10.4844, -66.9034, "Final Av. Principal de Las Mercedes, Caracas"
    ),
    "ttc": VenueCoords(
        10.4844, -66.9034, "Final Av. Principal de Las Mercedes, Caracas"
    ),
    "sala rios reyna": VenueCoords(
        10.4844, -66.9034, "Teatro Teresa Carreño, Las Mercedes"
    ),
    "sala jose felix ribas": VenueCoords(
        10.4844, -66.9034, "Teatro Teresa Carreño, Las Mercedes"
    ),
    "teatro alberto de paz y mateos": VenueCoords(
        10.5063, -66.8575, "Av. Mohedano con Calle El Carmen, Chacao"
    ),
    "teatro municipal": VenueCoords(
        10.4940, -66.8555, "Centro Cultural Chacao, Caracas"
    ),
    "teatro chacao": VenueCoords(10.4940, -66.8555, "Centro Cultural Chacao, Caracas"),
    "fundacion rajatabla": VenueCoords(
        10.4945, -66.8779, "CELARG, Av. Luis Roche, Altamira"
    ),
    "rajatabla": VenueCoords(10.4945, -66.8779, "CELARG, Av. Luis Roche, Altamira"),
    # ----- Cultural centers -----
    "centro cultural chacao": VenueCoords(
        10.4940, -66.8555, "Av. Guaicaipuro con Av. F. Fajardo, Chacao"
    ),
    "ccch": VenueCoords(
        10.4940, -66.8555, "Av. Guaicaipuro con Av. F. Fajardo, Chacao"
    ),
    "centro cultural de arte moderno": VenueCoords(
        10.4967, -66.8722, "Torre BOD, Av. Bolívar, Caracas"
    ),
    "ccam": VenueCoords(10.4967, -66.8722, "Torre BOD, Av. Bolívar, Caracas"),
    "centro de arte los galpones": VenueCoords(
        10.4638, -66.9146, "Av. Ávila con Sur 11, Los Galpones"
    ),
    "los galpones": VenueCoords(
        10.4638, -66.9146, "Av. Ávila con Sur 11, Los Galpones"
    ),
    "asociacion cultural humboldt": VenueCoords(
        10.4772, -66.8675, "Colinas de Los Caobos, Caracas"
    ),
    "celarg": VenueCoords(10.4945, -66.8779, "Av. Luis Roche, Altamira Norte"),
    "centro de estudios latinamericanos romulo gallegos": VenueCoords(
        10.4945, -66.8779, "Av. Luis Roche, Altamira Norte"
    ),
    "fundarte": VenueCoords(
        10.4870, -66.8878, "Parque del Este, frente a Fuente, Caracas"
    ),
    "hacienda la trinidad": VenueCoords(
        10.4500, -66.8515, "Calle Principal La Trinidad, Caracas"
    ),
    "hacienda la trinidad parque cultural": VenueCoords(
        10.4500, -66.8515, "Calle Principal La Trinidad, Caracas"
    ),
    "villa planchart": VenueCoords(
        10.3955, -66.8874, "Lomas de La Trinidad, Av. Luis Roche, Caracas"
    ),
    # ----- International cultural centers -----
    "centro cultural de espana en caracas": VenueCoords(
        10.4970, -66.8770, "Av. Rómulo Gallegos, cruce con Principal, Altamira"
    ),
    "ccecaracas": VenueCoords(
        10.4970, -66.8770, "Av. Rómulo Gallegos, cruce con Principal, Altamira"
    ),
    "alianza francesa": VenueCoords(
        10.4956, -66.8580, "Torre La Castellana, Av. Eugenio Mendoza"
    ),
    "alianza francesa de caracas": VenueCoords(
        10.4956, -66.8580, "Torre La Castellana, Av. Eugenio Mendoza"
    ),
    "goethe institut": VenueCoords(
        10.4958, -66.8733, "Centro Plaza, Av. Luis Roche, Altamira"
    ),
    "goethe institut venezuela": VenueCoords(
        10.4958, -66.8733, "Centro Plaza, Av. Luis Roche, Altamira"
    ),
    "instituto italiano de cultura": VenueCoords(
        10.4941, -66.8755, "Calle Arturo Uslar Pietri, Altamira"
    ),
    "instituto italiano de cultura de caracas": VenueCoords(
        10.4941, -66.8755, "Calle Arturo Uslar Pietri, Altamira"
    ),
    "iiccaracas": VenueCoords(10.4941, -66.8755, "Calle Arturo Uslar Pietri, Altamira"),
    # ----- Universities -----
    "direccion de cultura ucv": VenueCoords(
        10.4905, -66.9005, "Sala de Conciertos, Ciudad Universitaria UCV"
    ),
    "cultura ucv": VenueCoords(
        10.4905, -66.9005, "Sala de Conciertos, Ciudad Universitaria UCV"
    ),
    "universidad central de venezuela": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria, Caracas"
    ),
    "ucv": VenueCoords(10.4905, -66.9005, "Ciudad Universitaria, Caracas"),
    "ciudad universitaria": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria, Caracas"
    ),
    "direccion de cultura ucab": VenueCoords(
        10.5000, -66.8744, "Universidad Católica Andrés Bello, Montalbán"
    ),
    "cultura ucab": VenueCoords(
        10.5000, -66.8744, "Universidad Católica Andrés Bello, Montalbán"
    ),
    "universidad catolica andres bello": VenueCoords(
        10.5000, -66.8744, "Universidad Católica Andrés Bello, Montalbán"
    ),
    "ucab": VenueCoords(
        10.5000, -66.8744, "Universidad Católica Andrés Bello, Montalbán"
    ),
    "direccion de cultura usb": VenueCoords(
        10.4095, -66.8836, "Universidad Simón Bolívar, Sartenejas, Caracas"
    ),
    "cultura usb": VenueCoords(
        10.4095, -66.8836, "Universidad Simón Bolívar, Sartenejas, Caracas"
    ),
    "universidad simon bolivar": VenueCoords(
        10.4095, -66.8836, "Universidad Simón Bolívar, Sartenejas, Caracas"
    ),
    "usb": VenueCoords(
        10.4095, -66.8836, "Universidad Simón Bolívar, Sartenejas, Caracas"
    ),
    # ----- Shopping malls -----
    "sambil": VenueCoords(
        10.4915, -66.8536, "Av. Libertador, frente a Plaza Venezuela, Caracas"
    ),
    "sambil caracas": VenueCoords(
        10.4915, -66.8536, "Av. Libertador, frente a Plaza Venezuela, Caracas"
    ),
    "tolon": VenueCoords(10.4984, -66.8622, "Av. San Felipe con Av. La Salle, Caracas"),
    "tolon fashion mall": VenueCoords(
        10.4984, -66.8622, "Av. San Felipe con Av. La Salle, Caracas"
    ),
    "centro comercial lider": VenueCoords(
        10.4631, -66.9752, "Calle Real de Los Dos Caminos, Caracas"
    ),
    "cclider": VenueCoords(10.4631, -66.9752, "Calle Real de Los Dos Caminos, Caracas"),
    "cc el lider": VenueCoords(
        10.4631, -66.9752, "Calle Real de Los Dos Caminos, Caracas"
    ),
    "parque cerro verde": VenueCoords(
        10.4950, -66.8280, "Final Av. Río de Janeiro, El Cafetal"
    ),
    "parque comercial cerro verde": VenueCoords(
        10.4950, -66.8280, "Final Av. Río de Janeiro, El Cafetal"
    ),
    # ----- Hotels (frequently used as event venues) -----
    "hotel humboldt": VenueCoords(
        10.5418, -66.8836, "Parque Nacional El Ávila, Caracas"
    ),
    "hotel tamanaco": VenueCoords(
        10.4888, -66.8619, "Av. Principal Las Mercedes, Caracas"
    ),
    "hotel eurobuilding": VenueCoords(
        10.4860, -66.8559, "Calle La Guairita, Chuao, Caracas"
    ),
    "hotel eurobuilding caracas": VenueCoords(
        10.4860, -66.8559, "Calle La Guairita, Chuao, Caracas"
    ),
    "caracas music hall": VenueCoords(10.4958, -66.8580, "Torre La Castellana, Chacao"),
    "salon turmalina": VenueCoords(10.4860, -66.8559, "Hotel Eurobuilding, Chuao"),
    # ----- Outdoor / parks (concert venue) -----
    "concha acustica de bello monte": VenueCoords(
        10.4790, -66.8733, "Parque del Este, Bello Monte"
    ),
    "parque del este": VenueCoords(10.4790, -66.8733, "Parque del Este, Bello Monte"),
    # ----- Sports venues -----
    "estadio universitario ucv": VenueCoords(
        10.4858, -66.9101, "Ciudad Universitaria UCV, Caracas"
    ),
    "estadio universitario": VenueCoords(
        10.4858, -66.9101, "Ciudad Universitaria UCV, Caracas"
    ),
    "estadio olimpico de la ucv": VenueCoords(
        10.4858, -66.9101, "Ciudad Universitaria UCV, Caracas"
    ),
    "estadio olimpico de la universidad central": VenueCoords(
        10.4858, -66.9101, "Ciudad Universitaria UCV, Caracas"
    ),
    "estadio olimpico": VenueCoords(
        10.4858, -66.9101, "Ciudad Universitaria UCV, Caracas"
    ),
    "estadio monumental de caracas": VenueCoords(
        10.4955, -66.8800, "Av. Simón Planas, Caracas"
    ),
    "estadio monumental": VenueCoords(10.4955, -66.8800, "Av. Simón Planas, Caracas"),
    "inh hipodromo": VenueCoords(10.4697, -66.9036, "Hipódromo La Rinconada, Caracas"),
    "hipodromo la rinconada": VenueCoords(
        10.4697, -66.9036, "Hipódromo La Rinconada, Caracas"
    ),
    "la rinconada": VenueCoords(10.4697, -66.9036, "Hipódromo La Rinconada, Caracas"),
    "polideportivo pachencho romero": VenueCoords(
        10.4567, -66.9069, "Av. Principal La Rinconada, Caracas"
    ),
    "senadores de caracas": VenueCoords(
        10.4858, -66.9101, "Estadio Universitario UCV, Caracas"
    ),
    "senadores de caracas caracas": VenueCoords(
        10.4858, -66.9101, "Estadio Universitario UCV, Caracas"
    ),
    # ----- Museums -----
    "museo de arte contemporaneo": VenueCoords(
        10.4958, -66.8733, "Parque del Este, Caracas"
    ),
    "museo de bellas artes": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria, Caracas"
    ),
    "fundacion museos nacionales": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria, Caracas"
    ),
    "museo de ciencias": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria, Caracas"
    ),
    # ----- Theaters (extra) -----
    "teatro municipal de caracas": VenueCoords(
        10.5071, -66.9035, "Av. Lecuna, frente a Plaza Caracas, Caracas"
    ),
    "teatro principal de caracas": VenueCoords(
        10.5080, -66.9035, "Av. Lecuna, Caracas"
    ),
    "teatro luis peraza": VenueCoords(10.4330, -66.7842, "El Hatillo, Caracas"),
    "villa teresa": VenueCoords(10.4905, -66.8544, "La Castellana, Caracas"),
    # ----- Universities / cultural (extra names) -----
    **_aliases(
        VenueCoords(10.4905, -66.9005, "Ciudad Universitaria UCV, Caracas"),
        "ciudad universitaria ucv",
        "ciudad universitaria",
    ),
    **_aliases(
        VenueCoords(10.4095, -66.8836, "Universidad Simón Bolívar, Sartenejas"),
        "usb sartenejas",
        "universidad simon bolivar",
    ),
    "ucab campus": VenueCoords(
        10.5000, -66.8744, "Universidad Católica Andrés Bello, Montalbán"
    ),
    "universidad central de venezuela sala conciertos": VenueCoords(
        10.4905, -66.9005, "Aula Magna, Ciudad Universitaria UCV"
    ),
    "aula magna ucv": VenueCoords(
        10.4905, -66.9005, "Aula Magna, Ciudad Universitaria UCV"
    ),
    "celarg altamira": VenueCoords(
        10.4945, -66.8779, "CELARG, Av. Luis Roche, Altamira"
    ),
    # ----- International centers (alias forms) -----
    "goethe institut caracas": VenueCoords(
        10.4958, -66.8733, "Centro Plaza, Av. Luis Roche, Altamira"
    ),
    **_aliases(
        VenueCoords(10.4970, -66.8770, "Av. Rómulo Gallegos, Altamira"),
        "centro cultural de espana caracas",
        "centro cultural de espana en caracas",
    ),
    "centro cultural de arte moderno sala de conciertos": VenueCoords(
        10.4967, -66.8722, "Torre BOD, Av. Bolívar, Caracas"
    ),
    "centro cultural chacao sala experimental": VenueCoords(
        10.4940, -66.8555, "Centro Cultural Chacao, Av. Guaicaipuro"
    ),
    # ----- Malls / plazas / urban (alias forms) -----
    **_aliases(
        VenueCoords(10.4631, -66.9752, "Calle Real de Los Dos Caminos, Caracas"),
        "espacio caracas cc lider",
        "cc el lider",
    ),
    "plaza bolivar de el hatillo": VenueCoords(
        10.4308, -66.7841, "Calle Bolívar, El Hatillo, Caracas"
    ),
    "plaza de altamira": VenueCoords(10.4938, -66.8785, "Plaza Altamira, Caracas"),
    "los palos grandes": VenueCoords(
        10.5008, -66.8528, "Av. Francisco Fajardo, Los Palos Grandes"
    ),
    "chuao": VenueCoords(10.4861, -66.8559, "Av. Chuao, Caracas"),
    "quebrada honda": VenueCoords(10.4340, -66.7860, "El Hatillo, Caracas"),
    "secadero hacienda la trinidad": VenueCoords(
        10.4500, -66.8515, "Calle Principal La Trinidad, Caracas"
    ),
    "fundaprocura": VenueCoords(
        10.4870, -66.8878, "Fundarte, Parque del Este, Caracas"
    ),
    # ----- Music / arts orgs / galleries (curated approximations) -----
    "centro nacional de accion social por la musica": VenueCoords(
        10.4905, -66.9005, "El Sistema, Bellas Artes, Ciudad Universitaria"
    ),
    "el sistema": VenueCoords(
        10.4905, -66.9005, "El Sistema, Bellas Artes, Ciudad Universitaria"
    ),
    "abra caracas": VenueCoords(10.4920, -66.8860, "Av. México, Bellas Artes, Caracas"),
    "gbg arts": VenueCoords(10.4955, -66.8495, "GBG Arts, Los Palos Grandes"),
    "carmen araujo arte": VenueCoords(
        10.4970, -66.8547, "Carmen Araujo Arte, Av. Luis Roche, Chacao"
    ),
    # ----- Venues discovered in production crawl data -----
    "hotel jw marriott caracas": VenueCoords(
        10.5000, -66.8800, "Av. Venezuela con Mohedano, Marriott, Caracas"
    ),
    "hard rock cafe caracas": VenueCoords(
        10.5000, -66.8620, "Plazaisentium, Los Palos Grandes, Caracas"
    ),
    "hard rock cafe caracas caracas": VenueCoords(
        10.5000, -66.8620, "Plazaisentium, Los Palos Grandes, Caracas"
    ),
    "hotel melia caracas": VenueCoords(
        10.4900, -66.8800, "Av. Luis Roche, Meliá Caracas"
    ),
    "hotel hesperia wtc de valencia": VenueCoords(
        10.1620, -68.0070, "Av. Arquímedes, Valencia, Carabobo"
    ),
    "monumental stadium caracas": VenueCoords(
        10.4955, -66.8800, "Av. Simón Planas, Monumental, Caracas"
    ),
    "estadio monumental simon bolivar": VenueCoords(
        10.4955, -66.8800, "Av. Simón Planas, Monumental, Caracas"
    ),
    "plaza de la juventud caracas": VenueCoords(
        10.4880, -66.8800, "Plaza de la Juventud, El Calvario, Caracas"
    ),
    "teatro rajatabla": VenueCoords(
        10.4945, -66.8779, "CELARG, Av. Luis Roche, Altamira"
    ),
    "plaza de la juventud": VenueCoords(
        10.4880, -66.8800, "Plaza de la Juventud, El Calvario, Caracas"
    ),
    "cinética cultural": VenueCoords(10.4880, -66.8800, "El Calvario, Caracas"),
    "cinetica cultural": VenueCoords(10.4880, -66.8800, "El Calvario, Caracas"),
    "anfiteatro del sambil caracas": VenueCoords(
        10.4915, -66.8536, "Sambil Caracas, Av. Libertador"
    ),
    "anfiteatro del sambil caracas chacao": VenueCoords(
        10.4915, -66.8536, "Sambil Caracas, Av. Libertador"
    ),
    "pizpa comedy club": VenueCoords(10.4970, -66.8560, "Las Mercedes, Caracas"),
    "agenda cultural ccs": VenueCoords(
        10.4900, -66.8800, "Alcaldía de Caracas, Plaza Caracas"
    ),
    "quinta esmeralda": VenueCoords(
        10.4560, -66.8400, "Colinas de La Trinidad, Caracas"
    ),
    "la quinta live music bar": VenueCoords(
        10.4560, -66.8400, "Colinas de La Trinidad, Caracas"
    ),
    "concha acustica de la carlota": VenueCoords(
        10.4790, -66.8733, "Parque del Este, Bello Monte"
    ),
    "vista bar": VenueCoords(10.4915, -66.8536, "Sambil Caracas, Av. Libertador"),
    "secret location": VenueCoords(10.4900, -66.8800, "Caracas"),
    "salon salsa": VenueCoords(10.4900, -66.8800, "Caracas"),
    "campus de la ucab": VenueCoords(10.5000, -66.8744, "UCAB, Montalbán, Caracas"),
    "ccct": VenueCoords(10.4888, -66.8619, "Ciudad Tamanaco, Av. Libertador, Caracas"),
    "centro comercial ciudad tamanaco": VenueCoords(
        10.4888, -66.8619, "Ciudad Tamanaco, Av. Libertador, Caracas"
    ),
    "cc ciudad tamanaco": VenueCoords(
        10.4888, -66.8619, "Ciudad Tamanaco, Av. Libertador, Caracas"
    ),
    "hard rock cafe": VenueCoords(
        10.5000, -66.8620, "Plazaisentium, Los Palos Grandes, Caracas"
    ),
    "chacao caracas": VenueCoords(10.4940, -66.8555, "Chacao, Caracas"),
    "aula magna": VenueCoords(10.4905, -66.9005, "Ciudad Universitaria UCV, Caracas"),
    "aula magna de la universidad central de venezuela": VenueCoords(
        10.4905, -66.9005, "Ciudad Universitaria UCV, Caracas"
    ),
}


def _normalize(text: str | None) -> str:
    """Same normalization as ``event_processing._normalize_location_name``
    plus NFKD accent stripping.

    Lowercases, replaces punctuation with whitespace (so ``Goethe-Institut``
    becomes "goethe institut" not "goetheinstitut"), drops combining
    diacritics (``í`` -> ``i``), and collapses runs of whitespace. The
    keys in :data:`_KNOWN_VENUES` are kept in this same form so lookups
    match whatever the caller passes.
    """
    import re
    import unicodedata

    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_form = "".join(c for c in nfkd if not unicodedata.combining(c))
    spaced = re.sub(r"[^\w\s]", " ", ascii_form.lower())
    return " ".join(spaced.split())


def lookup_known_venue(name: str | None) -> VenueCoords | None:
    """Return curated coordinates for a venue name, or ``None`` if unknown.

    Tries ``name`` first, then progressively removes prefixes, suffixes,
    and segment separators until it either finds a curated hit or runs
    out of candidates. The fallback chain is:

      1. Exact normalized match.
      2. Strip leading "sala", "auditorio", "anfiteatro", "estadio".
      3. Strip trailing "caracas"/"caracas venezuela"/"CCS" suffix.
      4. Split on " - ", " | ", ", "; try first and last segments.
    """
    if not name:
        return None

    norm = _normalize(name)
    if not norm:
        return None

    candidates: list[str] = [norm]

    # Strip common venue prefixes.
    for prefix in ("sala ", "auditorio ", "anfiteatro ", "estadio "):
        if norm.startswith(prefix):
            candidates.append(norm[len(prefix) :])

    # Strip trailing "caracas" / city qualifier when nothing else matched.
    for suffix in (" caracas", " caracas venezuela", " ccs"):
        if norm.endswith(suffix) and len(norm) > len(suffix):
            candidates.append(norm[: -len(suffix)])

    # Split on common separators — first and last segments are tried.
    for separator in (" - ", " – ", " | ", ", "):
        if separator in norm:
            parts = [p.strip() for p in norm.split(separator)]
            if parts:
                candidates.append(parts[0])
            if len(parts) > 1:
                candidates.append(parts[-1])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate in _KNOWN_VENUES:
            return _KNOWN_VENUES[candidate]

    return None
