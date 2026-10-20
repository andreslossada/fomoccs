"""Event deduplication and merging helpers.

Pure helpers ported verbatim from ``pipeline/merger.py`` lines 17-87
(name normalization, stemming, and significant-word extraction).

This module intentionally has no database access; it only exposes pure
functions used by the event-merging service. See ``pipeline/merger.py``
for the original reference implementation.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.base import CrawlResultStatus, EventStatus, ExtractedEventStatus
from api.models.crawl import CrawlResult, ExtractedEvent, ExtractedEventLog
from api.models.event import Event, EventOccurrence, EventSource, EventTag, EventUrl
from api.models.location import Location
from api.models.tag import Tag

STOP_WORDS: frozenset[str] = frozenset(
    {"the", "and", "for", "with", "from", "into", "your"}
)

# Venezuelan cities outside the Caracas metropolitan area. National
# cultural sites (Fundarte, Teatro, CCAM) routinely publish listings for
# these; the extractor prompt asks the LLM to skip them, but that is not
# reliable enough on its own. Anything matching is logged and dropped so
# out-of-city events never reach the map (they have no resolvable
# coordinates and silently occupy feed slots).
OUT_OF_CITY_MARKERS: tuple[str, ...] = (
    # --- Capitales y ciudades principales ---
    "maracay",
    "valencia carabobo",
    "valencia",
    "barquisimeto",
    "punto fijo",
    "ciudad guayana",
    "puerto la cruz",
    "cumana",
    "maturin",
    "barinas",
    "merida",
    "tucupita",
    "coro",
    "ciudad bolivar",
    "maracaibo",
    # --- Otras ciudades ---
    "san juan de los morros",
    "san cristobal",
    "san fernando de apure",
    "calabozo",
    "puerto ordaz",
    "el tigre",
    "anaco",
    "caripito",
    "guanare",
    "acayurman",
    "turmero",
    "bocono",
    "la trinidad de orinoco",
    "coro falcon",
    "guigue",
    "valles del tuy",
    "santa teresa del tuy",
    "valle del tuy",
    "ciudad ojeda",
    "machiques",
    "la frontera",
    "san antonio del tachira",
    "capaneda",
    "piedras Blancas",
)


def is_out_of_caracas(*texts: str | None) -> bool:
    """Return True if any text names a city clearly outside Caracas.

    Used as a hard backstop after the LLM extraction step. Accents are
    folded (``Maracay`` written decomposed still matches) and matching is
    word-bounded so ``El Tigre`` doesn't fire on ``Tigres``.
    """
    import unicodedata

    for raw in texts:
        if not raw:
            continue
        # Fold accents so "Maracay" matches "Maracay" written with a
        # decomposed accent.
        folded = unicodedata.normalize("NFKD", raw.lower())
        plain = "".join(c for c in folded if not unicodedata.combining(c))
        for marker in OUT_OF_CITY_MARKERS + OUT_OF_CITY_VENUE_MARKERS:
            # Word-boundary match so "El Tigre" doesn't fire on
            # "Tigres" and "Guarenas" isn't caught by "guate".
            if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", plain):
                return True
    return False


# Venue-name fragments that only exist outside Caracas. Regional portals
# list national tours city by city, and the city is often only implied by
# the venue ("AMANECER VALLENATO" was at "Palacio de Eventos de Vzla"
# in Puerto Ordaz). These catch those cases, which the explicit city
# markers miss.
OUT_OF_CITY_VENUE_MARKERS: tuple[str, ...] = (
    "palacio de eventos de vzla",
    "palacio de eventos",
    "grato jardin de eventos",
    "gaduca eventos",
    "aten",
    "colegio loyola",
    "colegio loyola gumilla",
    "ateneo de punto fijo",
    "teatro luis hurtado",
    "casa sindical",
    "san marcos palace",
    "gran salon san marcos",
    "club sirio",
    "tibisay hotel",
    "tibisay",
    "trinitarias suites",
    "trinitarias",
    "tulio febres cordero",
    "centro cultural espanol cervantes",
    "centro cultural tulio febres",
    "aula magna de barquisimeto",
    "museo de arte moderno de barquisimeto",
)


SEMANTIC_EQUIVALENTS: dict[str, str] = {
    "dinner": "dine",
    "dining": "dine",
    "diner": "dine",
    # Day abbreviations -> full names
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "weds": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}

STEM_SUFFIXES: list[tuple[str, str]] = [
    ("ency", "enc"),  # residency -> residenc
    ("ence", "enc"),  # residence -> residenc
    ("ing", ""),  # running -> runn
    ("tion", "t"),  # creation -> creat
    ("sion", "s"),  # decision -> decis
    ("ies", "y"),  # stories -> story
    ("es", ""),  # boxes -> box
    ("s", ""),  # cats -> cat
]


def normalize_name_for_dedup(name: str) -> str:
    """Remove accents, punctuation, underscores, and extra whitespace.

    Returns a lowercase, whitespace-collapsed string suitable for
    deduplication comparisons.
    """
    # Normalize unicode to remove accents (é -> e, etc.)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))

    no_underscores = ascii_name.replace("_", "")
    # Replace punctuation with spaces (not just remove) to avoid word
    # concatenation, e.g. "Alice/Bob" -> "Alice Bob", not "AliceBob".
    no_punct = re.sub(r"[^\w\s]", " ", no_underscores.strip().lower())
    normalized = re.sub(r"\s+", " ", no_punct).strip()
    return normalized


def stem_word(word: str) -> str:
    """Apply semantic equivalents then a small set of suffix rules."""
    if word in SEMANTIC_EQUIVALENTS:
        return SEMANTIC_EQUIVALENTS[word]

    for suffix, replacement in STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + replacement
    return word


def _is_year(word: str) -> bool:
    """Return True if ``word`` is a 4-digit year in the 20XX range."""
    return len(word) == 4 and word.isdigit() and word.startswith("20")


def get_significant_words(name: str, *, stem: bool = False) -> frozenset[str]:
    """Return 3+ char words from ``name`` minus stop words and years.

    When ``stem`` is true, each word is passed through :func:`stem_word`
    before being returned.
    """
    norm = normalize_name_for_dedup(name)
    words = norm.split()

    result = {
        w for w in words if len(w) >= 3 and w not in STOP_WORDS and not _is_year(w)
    }
    if stem:
        result = {stem_word(w) for w in result}
    return frozenset(result)


KNOWN_PROGRAM_PREFIXES: tuple[str, ...] = ("FIDO",)

PRESENTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^.+?\s+presents?\s*:?\s*", re.IGNORECASE),
    re.compile(r"^.+?\s+productions?\s*:?\s*", re.IGNORECASE),
    re.compile(r"^hosted\s+by\s+.+?:\s*", re.IGNORECASE),
)

_BRACKETED_PREFIX_RE = re.compile(r"^\s*\[[^\]]+\]\s*")


def strip_common_prefixes(name: str) -> str:
    """Strip common prefixes that don't change event identity.

    Handles:
    - Bracketed prefixes: ``[member-only]``, ``[free]``, ``[virtual]``, etc.
    - Known event program prefixes: ``FIDO`` (Prospect Park dog events), etc.

    Examples:
        ``"[member-only] Sewing Machines"`` -> ``"Sewing Machines"``
        ``"FIDO Coffee Bark"`` -> ``"Coffee Bark"``
        ``"[FREE] Jazz in the Park"`` -> ``"Jazz in the Park"``
    """
    result = name.strip()
    result = _BRACKETED_PREFIX_RE.sub("", result)

    for prefix in KNOWN_PROGRAM_PREFIXES:
        pattern = rf"^{re.escape(prefix)}\s+"
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)

    return result.strip()


def extract_core_title(name: str) -> str:
    """Extract the core title by removing presenter prefixes and subtitles.

    Examples:
        ``"Manhattan Theatre Club Presents The Monsters"`` -> ``"The Monsters"``
        ``"The Monsters: a Sibling Love Story"`` -> ``"The Monsters"``
        ``"Lincoln Center Presents: Jazz at Midnight"`` -> ``"Jazz at Midnight"``
        ``"[member-only] Sewing Class"`` -> ``"Sewing Class"``
        ``"FIDO Coffee Bark"`` -> ``"Coffee Bark"``
    """
    result = strip_common_prefixes(name)

    for pattern in PRESENTER_PATTERNS:
        result = pattern.sub("", result)

    # Remove subtitles after colon, but keep if main title is too short.
    if ":" in result:
        main_title = result.split(":", 1)[0].strip()
        if len(main_title) >= 5:
            result = main_title

    return result.strip()


# Precompiled patterns for false-positive detection.
_TIME_END_RE: re.Pattern[str] = re.compile(r"\d{3,4}\s*(?:am|pm)$", re.IGNORECASE)
_NIGHT_RE: re.Pattern[str] = re.compile(r"night\s*(\d+)")
_EPISODE_RE: re.Pattern[str] = re.compile(r"ep(?:isode)?\.?\s*(\d+)", re.IGNORECASE)
NUMBERED_KEYWORDS: tuple[str, ...] = (
    "set",
    "part",
    "vol",
    "volume",
    "chapter",
    "session",
    "round",
)
_NUMBERED_RE_BY_KEYWORD: dict[str, re.Pattern[str]] = {
    kw: re.compile(rf"\b{kw}\.?\s*(\d+)", re.IGNORECASE) for kw in NUMBERED_KEYWORDS
}
_SEQ_RE: re.Pattern[str] = re.compile(r"(?:^|\|)\s*#?\s*(\d+)\s*(?:\||$)")
_VS_RE: re.Pattern[str] = re.compile(r"vs\.?\s+(.+?)(?:\s*-|$)", re.IGNORECASE)
_TIME_ANYWHERE_RE: re.Pattern[str] = re.compile(
    r"\b(\d{1,2}\s*\d{2}\s*(?:am|pm))\b", re.IGNORECASE
)


def is_false_positive(name1: str, name2: str) -> bool:
    """Return True if two "similar" names are actually distinct events.

    Ported verbatim from ``pipeline/merger.py`` lines 157-244. Catches
    cases where names share words but refer to different events:

    - Men's vs Women's sports
    - Different showtimes (6:00 PM vs 8:00 PM, anywhere in name)
    - Early vs Late sets
    - Different episode numbers
    - Different set/part/volume numbers
    - Different sports opponents
    """
    norm1 = normalize_name_for_dedup(name1)
    norm2 = normalize_name_for_dedup(name2)

    # Different gendered sports events (Men's vs Women's).
    if ("men" in norm1) != ("men" in norm2) or ("women" in norm1) != ("women" in norm2):
        return True

    # Different times at end (different showtimes).
    time1 = _TIME_END_RE.search(norm1)
    time2 = _TIME_END_RE.search(norm2)
    if time1 and time2 and time1.group() != time2.group():
        return True

    # Early vs Late sets.
    if ("early" in norm1) != ("early" in norm2) or ("late" in norm1) != (
        "late" in norm2
    ):
        return True

    # Different numbered nights/sessions.
    night1 = _NIGHT_RE.search(norm1)
    night2 = _NIGHT_RE.search(norm2)
    if night1 and night2 and night1.group(1) != night2.group(1):
        return True

    # Different episodes.
    ep1 = _EPISODE_RE.search(norm1)
    ep2 = _EPISODE_RE.search(norm2)
    if ep1 and ep2 and ep1.group(1) != ep2.group(1):
        return True

    # Different set/part/volume numbers.
    for keyword in NUMBERED_KEYWORDS:
        pattern = _NUMBERED_RE_BY_KEYWORD[keyword]
        match1 = pattern.search(norm1)
        match2 = pattern.search(norm2)
        if match1 and match2 and match1.group(1) != match2.group(1):
            return True

    # Different standalone sequence numbers after pipe/dash separators.
    seq1 = _SEQ_RE.findall(norm1)
    seq2 = _SEQ_RE.findall(norm2)
    if seq1 and seq2 and seq1 != seq2:
        return True

    # Different sports opponents (vs X vs vs Y).
    vs1 = _VS_RE.search(norm1)
    vs2 = _VS_RE.search(norm2)
    if vs1 and vs2:
        opponent1 = vs1.group(1).strip()
        opponent2 = vs2.group(1).strip()
        if (
            opponent1 != opponent2
            and opponent1 not in opponent2
            and opponent2 not in opponent1
        ):
            return True

    # Different times anywhere in name.
    times1 = set(_TIME_ANYWHERE_RE.findall(norm1))
    times2 = set(_TIME_ANYWHERE_RE.findall(norm2))
    if times1 and times2 and times1 != times2:
        return True

    return False


def are_names_similar(name1: str, name2: str) -> bool:
    """Return True if two event names are similar enough to be duplicates.

    Ported verbatim from ``pipeline/merger.py`` lines 247-361. Uses six
    strategies:

    1. Exact match after normalization.
    2. Match after stripping common prefixes (FIDO, [member-only], etc.).
    3. Substring matching for prefix/suffix variations.
    4. Core title extraction (removing presenter prefixes and subtitles),
       with a subtitle-match exception for colon-separated series titles.
    5. Word-based matching: subset or Jaccard >= 0.7.
    6. Stemmed word matching with asymmetric 0.75 containment.

    Also short-circuits to False for known false-positive patterns via
    :func:`is_false_positive`.
    """
    # False-positive short-circuit.
    if is_false_positive(name1, name2):
        return False

    norm1 = normalize_name_for_dedup(name1)
    norm2 = normalize_name_for_dedup(name2)

    # 1. Exact match after normalization.
    if norm1 == norm2:
        return True

    # 2. Match after stripping common prefixes.
    stripped1 = normalize_name_for_dedup(strip_common_prefixes(name1))
    stripped2 = normalize_name_for_dedup(strip_common_prefixes(name2))
    if stripped1 == stripped2:
        return True

    # 3. Substring match for prefix/suffix variations.
    if len(norm1) >= 5 and len(norm2) >= 5:
        if norm1 in norm2 or norm2 in norm1:
            return True

    if len(stripped1) >= 5 and len(stripped2) >= 5:
        if stripped1 in stripped2 or stripped2 in stripped1:
            return True

    # 4. Core title comparison (with subtitle exception for series titles).
    core1 = extract_core_title(name1)
    core2 = extract_core_title(name2)
    skip_core_title_match = False
    if core1 and core2:
        norm_core1 = normalize_name_for_dedup(core1)
        norm_core2 = normalize_name_for_dedup(core2)
        if norm_core1 == norm_core2:
            # Series:episode format — require subtitle similarity too.
            if ":" in name1 and ":" in name2:
                subtitle1 = name1.split(":", 1)[1].strip()
                subtitle2 = name2.split(":", 1)[1].strip()
                if subtitle1 and subtitle2:
                    norm_sub1 = normalize_name_for_dedup(subtitle1)
                    norm_sub2 = normalize_name_for_dedup(subtitle2)
                    if (
                        norm_sub1 == norm_sub2
                        or norm_sub1 in norm_sub2
                        or norm_sub2 in norm_sub1
                    ):
                        return True
                    skip_core_title_match = True
                else:
                    return True
            else:
                return True
        if not skip_core_title_match and len(norm_core1) >= 5 and len(norm_core2) >= 5:
            if norm_core1 in norm_core2 or norm_core2 in norm_core1:
                return True

    # 5. Word-based similarity with unstemmed words.
    words1 = get_significant_words(name1)
    words2 = get_significant_words(name2)

    if words1 and words2:
        if words1.issubset(words2) or words2.issubset(words1):
            return True

        intersection = words1 & words2
        union = words1 | words2
        if len(intersection) / len(union) >= 0.7:
            return True

    # 6. Stemmed word matching.
    stemmed1 = get_significant_words(name1, stem=True)
    stemmed2 = get_significant_words(name2, stem=True)

    if stemmed1 and stemmed2:
        if stemmed1.issubset(stemmed2) or stemmed2.issubset(stemmed1):
            return True

        intersection = stemmed1 & stemmed2
        union = stemmed1 | stemmed2
        if len(intersection) / len(union) >= 0.7:
            return True

        # Asymmetric containment: 75%+ of shorter name's words in the longer.
        shorter, longer = (
            (stemmed1, stemmed2)
            if len(stemmed1) <= len(stemmed2)
            else (stemmed2, stemmed1)
        )
        if len(shorter) >= 2 and len(intersection) / len(shorter) >= 0.75:
            return True

    return False


# ---------------------------------------------------------------------------
# JSONB parsing helpers (ported from pipeline/merger.py lines 364-375, 563-610)
# ---------------------------------------------------------------------------

# How far ahead an occurrence may fall and still be worth keeping.
# Must match the window the public feed serves
# (``backend/api/routers/feed.py`` ``timedelta(days=180)``) and the
# frontend default (``src/js/script.js`` ``END_DATE``). When this was 90
# while the feed served 180, events between day 91 and 180 were dropped at
# merge time and could never reach the map.
FUTURE_LIMIT_DAYS: int = 180

# Grace period before an event is archived: an occurrence that ended
# within the last N days still counts as recent. Without it, an event
# whose last date slips past as time advances is archived the moment it
# ends, even while a source is slow to re-crawl.
ARCHIVE_GRACE_DAYS: int = 14


@dataclass(frozen=True)
class ParsedOccurrence:
    """A single parsed occurrence from the JSONB ``occurrences`` column."""

    start_date: date
    start_time: str | None
    end_date: date | None
    end_time: str | None


def _parse_jsonb(value: object) -> list[Any] | dict[str, Any] | None:
    """Parse a JSONB value that may be a string, dict/list, or None.

    Ported from ``pipeline/merger.py`` lines 364-375.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, (dict, list)):
            return parsed
        return None
    return None


def _parse_date(value: object) -> date | None:
    """Safely parse a YYYY-MM-DD string into a date, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_time_str(value: object) -> str | None:
    """Return the value as a string if non-empty, else None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_occurrences(raw: object, *, today: date) -> list[ParsedOccurrence]:
    """Parse and filter the JSONB ``occurrences`` column.

    Keeps only occurrences whose ``start_date`` falls within
    ``[today, today + FUTURE_LIMIT_DAYS]``. Malformed entries are skipped.

    Ported from ``pipeline/merger.py`` lines 563-605.
    """
    parsed = _parse_jsonb(raw) or []
    if not isinstance(parsed, list):
        return []

    future_limit = today + timedelta(days=FUTURE_LIMIT_DAYS)
    result: list[ParsedOccurrence] = []

    for occ in parsed:
        if not isinstance(occ, dict):
            continue
        start_date = _parse_date(occ.get("start_date"))
        if start_date is None:
            continue
        if not (today <= start_date <= future_limit):
            continue

        result.append(
            ParsedOccurrence(
                start_date=start_date,
                start_time=_parse_time_str(occ.get("start_time")),
                end_date=_parse_date(occ.get("end_date")),
                end_time=_parse_time_str(occ.get("end_time")),
            )
        )

    return result


def parse_tags(raw: object) -> list[str]:
    """Parse the JSONB ``tags`` column into a clean list of strings.

    Drops non-string entries and strips whitespace from each tag. Empty
    strings (after stripping) are also dropped.

    Ported from ``pipeline/merger.py`` lines 607-610.
    """
    parsed = _parse_jsonb(raw) or []
    if not isinstance(parsed, list):
        return []

    result: list[str] = []
    for tag in parsed:
        if not isinstance(tag, str):
            continue
        stripped = tag.strip()
        if stripped:
            result.append(stripped)
    return result


# ---------------------------------------------------------------------------
# Dedup index — ported from ``pipeline/merger.py`` lines 437-520.
# ---------------------------------------------------------------------------

RECENT_BUFFER_DAYS: int = 10


@dataclass(frozen=True)
class EventCandidate:
    """A lightweight reference to an existing event for dedup matching."""

    id: int
    name: str


@dataclass
class DedupIndex:
    """Three lookup indexes plus a per-event set of future occurrence dates.

    Mirrors the in-memory maps built at the top of
    ``pipeline/merger.py:merge_extracted_events`` so the async backend can
    perform the same dedup lookups by location_id, coordinates, or
    source_id.
    """

    by_location_id: dict[int, list[EventCandidate]] = field(default_factory=dict)
    by_coords: dict[tuple[float, float], list[EventCandidate]] = field(
        default_factory=dict
    )
    by_source_id: dict[int, list[EventCandidate]] = field(default_factory=dict)
    dates_by_event_id: dict[int, set[str]] = field(default_factory=dict)

    def add(
        self,
        candidate: EventCandidate,
        *,
        location_id: int | None,
        lat: float | None,
        lng: float | None,
        source_id: int | None,
        dates: set[str],
    ) -> None:
        """Insert ``candidate`` into every index for which it has a key."""
        if location_id is not None:
            self.by_location_id.setdefault(location_id, []).append(candidate)

        if lat is not None and lng is not None:
            key = (round(float(lat), 5), round(float(lng), 5))
            self.by_coords.setdefault(key, []).append(candidate)

        if source_id is not None:
            self.by_source_id.setdefault(source_id, []).append(candidate)

        self.dates_by_event_id[candidate.id] = set(dates)


async def load_dedup_index(db: AsyncSession, *, today: date) -> DedupIndex:
    """Build a :class:`DedupIndex` of active events with future occurrences.

    Ported from ``pipeline/merger.py`` lines 437-520. Selects active,
    non-soft-deleted events whose occurrences fall within
    ``[today - RECENT_BUFFER_DAYS, today + FUTURE_LIMIT_DAYS]``, and
    populates the three lookup maps plus the per-event date set used for
    downstream dedup.
    """
    recent_cutoff = today - timedelta(days=RECENT_BUFFER_DAYS)
    future_limit = today + timedelta(days=FUTURE_LIMIT_DAYS)

    stmt = (
        select(
            Event.id,
            Event.name,
            Event.location_id,
            Location.lat,
            Location.lng,
            EventOccurrence.start_date,
        )
        .join(EventOccurrence, EventOccurrence.event_id == Event.id)
        .join(Location, Location.id == Event.location_id, isouter=True)
        .where(
            Event.status == EventStatus.active,
            Event.deleted_at.is_(None),
            EventOccurrence.start_date >= recent_cutoff,
            EventOccurrence.start_date <= future_limit,
        )
    )
    rows = (await db.execute(stmt)).all()

    index = DedupIndex()
    # Accumulate per-event metadata before touching indexes so we only call
    # ``add`` once per event (preserving the merger's de-duplication
    # behaviour across multiple matching occurrences).
    per_event: dict[int, dict[str, Any]] = {}
    for event_id, name, location_id, lat, lng, start_date in rows:
        entry = per_event.setdefault(
            event_id,
            {
                "name": name,
                "location_id": location_id,
                "lat": lat,
                "lng": lng,
                "dates": set(),
            },
        )
        if start_date is not None:
            entry["dates"].add(str(start_date))

    if not per_event:
        return index

    # Second query: source_ids for these events.
    source_stmt = select(EventSource.event_id, EventSource.source_id).where(
        EventSource.event_id.in_(per_event.keys()),
        EventSource.source_id.is_not(None),
    )
    source_rows = (await db.execute(source_stmt)).all()

    source_ids_by_event: dict[int, list[int]] = {}
    for event_id, source_id in source_rows:
        source_ids_by_event.setdefault(event_id, []).append(source_id)

    for event_id, meta in per_event.items():
        candidate = EventCandidate(id=event_id, name=meta["name"])
        source_ids = source_ids_by_event.get(event_id, [])
        # Insert into location/coord indexes once per event.
        index.add(
            candidate,
            location_id=meta["location_id"],
            lat=meta["lat"],
            lng=meta["lng"],
            source_id=None,
            dates=meta["dates"],
        )
        # Then append to the source index once per (event, source).
        for source_id in source_ids:
            index.by_source_id.setdefault(source_id, []).append(candidate)

    return index


# ---------------------------------------------------------------------------
# Match selection and audit logging
# (ported from ``pipeline/merger.py`` lines 378-384, 617-652)
# ---------------------------------------------------------------------------


def find_best_match(
    name: str,
    extracted_dates: set[str],
    candidates: list[EventCandidate],
    dates_by_event_id: dict[int, set[str]],
) -> int | None:
    """Find the best matching event id among ``candidates``.

    Requires a date overlap between ``extracted_dates`` and each
    candidate's occurrence dates, then uses :func:`are_names_similar` to
    filter similar names. Prefers an exact normalized-name match; if none
    is found, returns the first partial match.

    Ported from ``pipeline/merger.py`` lines 621-632.
    """
    norm_name = normalize_name_for_dedup(name)
    best_id: int | None = None
    for existing in candidates:
        existing_dates = dates_by_event_id.get(existing.id, set())
        if not (extracted_dates & existing_dates):
            continue
        if not are_names_similar(name, existing.name):
            continue
        if normalize_name_for_dedup(existing.name) == norm_name:
            return existing.id
        if best_id is None:
            best_id = existing.id
    return best_id


def select_matched_event_id(
    *,
    name: str,
    extracted_dates: set[str],
    location_id: int | None,
    lat: float | None,
    lng: float | None,
    source_id: int,
    index: DedupIndex,
) -> int | None:
    """Return an existing event id using location -> coords -> source fallback.

    Ported from ``pipeline/merger.py`` lines 634-652. Tries, in order:

    1. Exact ``location_id`` lookup in :attr:`DedupIndex.by_location_id`.
    2. Rounded ``(lat, lng)`` lookup in :attr:`DedupIndex.by_coords`.
    3. ``source_id`` lookup in :attr:`DedupIndex.by_source_id`.

    Each candidate list is resolved via :func:`find_best_match`.
    """
    # 1. Try matching by location_id first (most precise and reliable).
    if location_id is not None and location_id in index.by_location_id:
        matched = find_best_match(
            name,
            extracted_dates,
            index.by_location_id[location_id],
            index.dates_by_event_id,
        )
        if matched is not None:
            return matched

    # 2. Fallback: match by rounded coordinates.
    if lat is not None and lng is not None:
        key = (round(float(lat), 5), round(float(lng), 5))
        if key in index.by_coords:
            matched = find_best_match(
                name,
                extracted_dates,
                index.by_coords[key],
                index.dates_by_event_id,
            )
            if matched is not None:
                return matched

    # 3. Last-resort fallback: match by source_id.
    if source_id in index.by_source_id:
        matched = find_best_match(
            name,
            extracted_dates,
            index.by_source_id[source_id],
            index.dates_by_event_id,
        )
        if matched is not None:
            return matched

    return None


async def log_extracted_event(
    db: AsyncSession,
    *,
    extracted_event_id: int,
    status: ExtractedEventStatus,
    event_id: int | None = None,
    message: str | None = None,
) -> None:
    """Insert an :class:`ExtractedEventLog` row. Does not commit.

    Ported from ``pipeline/merger.py`` lines 378-384.
    """
    db.add(
        ExtractedEventLog(
            extracted_event_id=extracted_event_id,
            status=status,
            event_id=event_id,
            message=message,
        )
    )


# ---------------------------------------------------------------------------
# Async writers — ported from ``pipeline/merger.py`` lines 654-802.
# ---------------------------------------------------------------------------


@dataclass
class ExtractedEventInput:
    """Input payload for :func:`merge_into_existing_event` and
    :func:`create_new_event`.

    Mirrors the per-row state used by ``pipeline/merger.py`` when iterating
    over ``new_extracted_events``.
    """

    ee_id: int
    name: str
    short_name: str | None
    description: str | None
    emoji: str | None
    sublocation: str | None
    location_id: int | None
    url: str | None
    source_id: int
    lat: float | None
    lng: float | None
    occurrences: list[ParsedOccurrence]
    tags: list[str]


async def merge_into_existing_event(
    db: AsyncSession,
    *,
    event_id: int,
    extracted: ExtractedEventInput,
) -> None:
    """Merge an extracted event into an existing event.

    Ported from ``pipeline/merger.py`` lines 654-702. Mutations performed:

    - Un-archive the event if currently archived.
    - Append the extracted ``url`` (truncated to 2000 chars) to ``event_urls``
      if not already present for this event.
    - Set ``events.location_id`` when the target event has none and the
      extracted payload provides one.
    - Insert a non-primary :class:`EventSource` linking the extracted event
      to the target event.

    Caller owns the transaction; this function does not commit.
    """
    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none()
    if event is None:
        return

    if event.status == EventStatus.archived and event.deleted_at is None:
        event.status = EventStatus.active

    if extracted.url:
        truncated_url = extracted.url[:2000]
        existing_url_id = (
            await db.execute(
                select(EventUrl.id).where(
                    EventUrl.event_id == event_id,
                    EventUrl.url == truncated_url,
                )
            )
        ).scalar_one_or_none()
        if existing_url_id is None:
            db.add(EventUrl(event_id=event_id, url=truncated_url))

    if event.location_id is None and extracted.location_id is not None:
        event.location_id = extracted.location_id

    db.add(
        EventSource(
            event_id=event_id,
            extracted_event_id=extracted.ee_id,
            source_id=extracted.source_id,
            is_primary=False,
        )
    )


async def create_new_event(
    db: AsyncSession,
    *,
    extracted: ExtractedEventInput,
) -> int:
    """Create a new :class:`Event` and all related rows.

    Ported from ``pipeline/merger.py`` lines 704-782. Inserts, in order:

    - The :class:`Event` itself (field lengths truncated to match the PHP
      migration).
    - One :class:`EventOccurrence` per entry in ``extracted.occurrences``.
    - An :class:`EventUrl` row when ``extracted.url`` is set.
    - One :class:`Tag` per tag name (created on demand) and a matching
      :class:`EventTag` link row.
    - A primary :class:`EventSource` linking the extracted event to the
      new event.

    Returns the new event id. Caller owns the transaction; this function
    does not commit.
    """
    if extracted.location_id is None:
        raise ValueError("create_new_event requires extracted.location_id")

    event = Event(
        name=extracted.name[:500],
        short_name=extracted.short_name[:255] if extracted.short_name else None,
        description=extracted.description,
        emoji=extracted.emoji[:10] if extracted.emoji else None,
        location_id=extracted.location_id,
        sublocation=extracted.sublocation[:255] if extracted.sublocation else None,
    )
    db.add(event)
    await db.flush()
    new_event_id = event.id

    for occ in extracted.occurrences:
        db.add(
            EventOccurrence(
                event_id=new_event_id,
                start_date=occ.start_date,
                start_time=occ.start_time,
                end_date=occ.end_date,
                end_time=occ.end_time,
            )
        )

    if extracted.url:
        db.add(EventUrl(event_id=new_event_id, url=extracted.url[:2000]))

    for tag_name in extracted.tags:
        if not tag_name:
            continue
        truncated = tag_name[:100]
        tag_id = (
            await db.execute(select(Tag.id).where(Tag.name == truncated))
        ).scalar_one_or_none()
        if tag_id is None:
            tag = Tag(name=truncated)
            db.add(tag)
            await db.flush()
            tag_id = tag.id

        existing_link = (
            await db.execute(
                select(EventTag.event_id).where(
                    EventTag.event_id == new_event_id,
                    EventTag.tag_id == tag_id,
                )
            )
        ).scalar_one_or_none()
        if existing_link is None:
            db.add(EventTag(event_id=new_event_id, tag_id=tag_id))
            await db.flush()

    db.add(
        EventSource(
            event_id=new_event_id,
            extracted_event_id=extracted.ee_id,
            source_id=extracted.source_id,
            is_primary=True,
        )
    )
    await db.flush()

    return new_event_id


# ---------------------------------------------------------------------------
# Top-level orchestrator and 14-day archiving
# (ported from ``pipeline/merger.py`` lines 387-431, 525-562, 808-868
# and ``pipeline/db.py:archive_outdated_events`` lines 447-548)
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """Return value of :func:`merge_extracted_events`."""

    new_events_count: int
    merged_count: int
    archived_count: int


async def archive_outdated_events(
    db: AsyncSession,
    *,
    source_id: int,
    today: date,
) -> tuple[int, list[tuple[int, str, date]]]:
    """Archive events whose sources no longer list them in their latest crawl.

    An active event linked to ``source_id`` is archived when, for **every**
    source that has ever linked to it, the most recent crawl result from
    that source (in ``processed``/``extracted`` status) does not reference
    the event. A 14-day grace period applies: events with any occurrence
    whose ``start_date >= today + 14 days`` are preserved.

    Returns a tuple of ``(archived_count, upcoming_warnings)`` where
    ``upcoming_warnings`` is a list of ``(event_id, name, next_occurrence)``
    tuples for events that were archived despite having upcoming
    occurrences (those strictly after ``today`` but strictly before the
    14-day grace threshold).
    """
    # Candidate events: active, non-deleted, linked via EventSource to source_id.
    candidate_stmt = (
        select(Event.id, Event.name)
        .join(EventSource, EventSource.event_id == Event.id)
        .where(
            Event.status == EventStatus.active,
            Event.deleted_at.is_(None),
            EventSource.source_id == source_id,
        )
        .distinct()
    )
    candidate_rows = (await db.execute(candidate_stmt)).all()
    if not candidate_rows:
        return 0, []

    candidate_ids = [row[0] for row in candidate_rows]
    names_by_id: dict[int, str] = {row[0]: row[1] for row in candidate_rows}

    # All (event_id, source_id, extracted_event_id) triples for candidate events.
    links_stmt = select(
        EventSource.event_id,
        EventSource.source_id,
        EventSource.extracted_event_id,
    ).where(EventSource.event_id.in_(candidate_ids))
    link_rows = (await db.execute(links_stmt)).all()

    # Map: event_id -> set of source_ids it's linked to.
    sources_by_event: dict[int, set[int]] = {}
    # Map: (event_id, source_id) -> set of extracted_event_ids
    ee_by_event_source: dict[tuple[int, int], set[int]] = {}
    all_source_ids: set[int] = set()
    for event_id, src_id, ee_id in link_rows:
        if src_id is None:
            continue
        sources_by_event.setdefault(event_id, set()).add(src_id)
        all_source_ids.add(src_id)
        if ee_id is not None:
            ee_by_event_source.setdefault((event_id, src_id), set()).add(ee_id)

    # Latest (by id) crawl_result per source in processed/extracted status.
    latest_cr_by_source: dict[int, int] = {}
    if all_source_ids:
        latest_stmt = (
            select(
                CrawlResult.source_id,
                func.max(CrawlResult.id).label("max_id"),
            )
            .where(
                CrawlResult.source_id.in_(all_source_ids),
                CrawlResult.status.in_(
                    (CrawlResultStatus.processed, CrawlResultStatus.extracted)
                ),
            )
            .group_by(CrawlResult.source_id)
        )
        for src_id, max_id in (await db.execute(latest_stmt)).all():
            if max_id is not None:
                latest_cr_by_source[src_id] = max_id

    # For each latest crawl_result, pull the set of extracted_event_ids it produced.
    ees_in_latest_cr: dict[int, set[int]] = {}
    if latest_cr_by_source:
        ee_stmt = select(ExtractedEvent.id, ExtractedEvent.crawl_result_id).where(
            ExtractedEvent.crawl_result_id.in_(latest_cr_by_source.values())
        )
        for ee_id_val, cr_id in (await db.execute(ee_stmt)).all():
            ees_in_latest_cr.setdefault(cr_id, set()).add(ee_id_val)

    # Occurrences per candidate event — for grace period + upcoming warnings.
    occ_stmt = select(EventOccurrence.event_id, EventOccurrence.start_date).where(
        EventOccurrence.event_id.in_(candidate_ids)
    )
    occs_by_event: dict[int, list[date]] = {}
    for event_id, start_date in (await db.execute(occ_stmt)).all():
        if start_date is not None:
            occs_by_event.setdefault(event_id, []).append(start_date)

    grace_threshold = today + timedelta(days=ARCHIVE_GRACE_DAYS)

    archived_count = 0
    upcoming_warnings: list[tuple[int, str, date]] = []

    for event_id in candidate_ids:
        event_sources = sources_by_event.get(event_id, set())
        if not event_sources:
            continue

        # An event is "still seen" if ANY of its linked sources' latest
        # crawl contains any of its extracted_events.
        still_seen = False
        for src_id in event_sources:
            latest_cr = latest_cr_by_source.get(src_id)
            if latest_cr is None:
                # No latest crawl -> treat source as "not seen here".
                continue
            latest_ees = ees_in_latest_cr.get(latest_cr, set())
            event_ees = ee_by_event_source.get((event_id, src_id), set())
            if event_ees & latest_ees:
                still_seen = True
                break
        if still_seen:
            continue

        # 14-day grace: preserve if any occurrence is on/after today+14d.
        occs = occs_by_event.get(event_id, [])
        if any(d >= grace_threshold for d in occs):
            continue

        # Collect next-upcoming-occurrence warning (strictly > today).
        future = sorted(d for d in occs if d > today)
        if future:
            upcoming_warnings.append((event_id, names_by_id[event_id], future[0]))

        # Archive in-place.
        event_obj = (
            await db.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()
        if event_obj is None:
            continue
        event_obj.status = EventStatus.archived
        archived_count += 1

    await db.flush()
    return archived_count, upcoming_warnings


async def merge_extracted_events(
    db: AsyncSession,
    *,
    crawl_job_id: int | None = None,
    today: date | None = None,
) -> MergeResult:
    """Top-level orchestrator: merge unprocessed extracted events and archive.

    Loads every :class:`ExtractedEvent` whose parent crawl result is in
    ``processed`` status and which has no :class:`EventSource` row yet,
    runs each through :func:`select_matched_event_id` and either
    :func:`merge_into_existing_event` or :func:`create_new_event`, updates
    an in-memory dedup index so within-batch duplicates match, and finally
    archives outdated events per affected source.

    The caller owns the transaction; the orchestrator only calls
    :meth:`AsyncSession.flush`.
    """
    if today is None:
        today = date.today()

    # Load unprocessed extracted events joined with their crawl_result
    # (for source_id) and optional location (for lat/lng). Left-join
    # EventSource to filter out already-processed rows.
    unprocessed_stmt = (
        select(
            ExtractedEvent.id,
            ExtractedEvent.name,
            ExtractedEvent.short_name,
            ExtractedEvent.description,
            ExtractedEvent.emoji,
            ExtractedEvent.sublocation,
            ExtractedEvent.location_id,
            ExtractedEvent.url,
            CrawlResult.source_id,
            Location.lat,
            Location.lng,
            ExtractedEvent.occurrences,
            ExtractedEvent.tags,
            Location.name,
        )
        .join(CrawlResult, CrawlResult.id == ExtractedEvent.crawl_result_id)
        .join(Location, Location.id == ExtractedEvent.location_id, isouter=True)
        .outerjoin(EventSource, EventSource.extracted_event_id == ExtractedEvent.id)
        .where(
            CrawlResult.status == CrawlResultStatus.processed,
            EventSource.id.is_(None),
        )
    )
    rows = (await db.execute(unprocessed_stmt)).all()

    if not rows:
        return MergeResult(new_events_count=0, merged_count=0, archived_count=0)

    index = await load_dedup_index(db, today=today)

    new_events_count = 0
    merged_count = 0
    affected_source_ids: set[int] = set()

    for row in rows:
        (
            ee_id,
            name,
            short_name,
            description,
            emoji,
            sublocation,
            location_id,
            url,
            source_id,
            lat,
            lng,
            raw_occurrences,
            raw_tags,
            location_name,
        ) = row

        if not name:
            continue

        # source_id is a system invariant.
        if source_id is None:
            raise RuntimeError(
                f"extracted_event {ee_id} has no source_id via crawl_results; "
                "this violates a system invariant"
            )

        affected_source_ids.add(source_id)

        # Hard backstop for events outside the Caracas metro area. The
        # extractor prompt asks the LLM to skip them, but national portals
        # (Fundarte, Teatro, CCAM) mix in other cities often enough that
        # prompt-only filtering leaked Maracay/Barquisimeto/Punto Fijo
        # listings into the feed. They can never geocode to Caracas, so
        # drop them here rather than let them occupy feed slots.
        if is_out_of_caracas(name, sublocation, url, location_name):
            await log_extracted_event(
                db,
                extracted_event_id=ee_id,
                status=ExtractedEventStatus.skipped_out_of_city,
                message=(
                    f"Outside Caracas metro area: name={name!r} "
                    f"sublocation={sublocation!r} location={location_name!r} "
                    f"url={url!r}"
                ),
            )
            continue

        if location_id is None:
            await log_extracted_event(
                db,
                extracted_event_id=ee_id,
                status=ExtractedEventStatus.skipped_no_location,
                message="No location_id on extracted event",
            )
            continue

        valid_occurrences = parse_occurrences(raw_occurrences, today=today)
        if not valid_occurrences:
            await log_extracted_event(
                db,
                extracted_event_id=ee_id,
                status=ExtractedEventStatus.skipped_no_occurrences,
                message="No valid future occurrences",
            )
            continue

        tags = parse_tags(raw_tags)
        lat_f = float(lat) if lat is not None else None
        lng_f = float(lng) if lng is not None else None

        extracted = ExtractedEventInput(
            ee_id=ee_id,
            name=name,
            short_name=short_name,
            description=description,
            emoji=emoji,
            sublocation=sublocation,
            location_id=location_id,
            url=url,
            source_id=source_id,
            lat=lat_f,
            lng=lng_f,
            occurrences=valid_occurrences,
            tags=tags,
        )

        extracted_dates = {
            str(occ.start_date) for occ in valid_occurrences if occ.start_date
        }

        matched_event_id = select_matched_event_id(
            name=name,
            extracted_dates=extracted_dates,
            location_id=location_id,
            lat=lat_f,
            lng=lng_f,
            source_id=source_id,
            index=index,
        )

        if matched_event_id is not None:
            await merge_into_existing_event(
                db, event_id=matched_event_id, extracted=extracted
            )
            await log_extracted_event(
                db,
                extracted_event_id=ee_id,
                status=ExtractedEventStatus.merged,
                event_id=matched_event_id,
                message=f"Merged with existing event {matched_event_id}",
            )
            await db.flush()
            merged_count += 1

            # Append this source to the dedup index so within-batch
            # duplicates from other sources still match.
            existing = next(
                (
                    c
                    for c in index.by_source_id.get(source_id, [])
                    if c.id == matched_event_id
                ),
                None,
            )
            if existing is None:
                matched_name = next(
                    (
                        c.name
                        for candidates in index.by_location_id.values()
                        for c in candidates
                        if c.id == matched_event_id
                    ),
                    name,
                )
                index.by_source_id.setdefault(source_id, []).append(
                    EventCandidate(id=matched_event_id, name=matched_name)
                )
            # Widen the candidate's known dates so later rows in the same
            # batch can still match via date overlap.
            dates = index.dates_by_event_id.setdefault(matched_event_id, set())
            dates.update(extracted_dates)
        else:
            new_event_id = await create_new_event(db, extracted=extracted)
            await log_extracted_event(
                db,
                extracted_event_id=ee_id,
                status=ExtractedEventStatus.created,
                event_id=new_event_id,
                message=f"Created new event {new_event_id}",
            )
            await db.flush()
            new_events_count += 1

            candidate = EventCandidate(id=new_event_id, name=name)
            index.add(
                candidate,
                location_id=location_id,
                lat=lat_f,
                lng=lng_f,
                source_id=source_id,
                dates=extracted_dates,
            )

    # Archive outdated events for every source touched by this batch.
    total_archived = 0
    for src_id in affected_source_ids:
        archived, _warnings = await archive_outdated_events(
            db, source_id=src_id, today=today
        )
        total_archived += archived

    await db.flush()
    return MergeResult(
        new_events_count=new_events_count,
        merged_count=merged_count,
        archived_count=total_archived,
    )
