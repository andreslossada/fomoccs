"""Event-processing helpers ported from ``pipeline/processor.py``.

This module contains the synchronous pure helpers (short-name generation,
emoji extraction) as well as the async DB-touching services
(``resolve_location``, ``load_tag_rules``, ``process_tags``,
``should_skip_for_tags``) used by the backend event-processing consumer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.base import TagRuleType
from api.models.location import Location
from api.models.tag import TagRule
from api.tasks.geocoding import geocode_location

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blocked emoji — ported verbatim from pipeline/processor.py lines 57-77.
# These render as plain boxes/squares and are treated as "no emoji".
# ---------------------------------------------------------------------------
BLOCKED_EMOJI: frozenset[str] = frozenset(
    {
        "\u2b1c",  # ⬜
        "\u25a1",  # □
        "\u25fb",  # ◻
        "\u2b1b",  # ⬛
        "\u25a0",  # ■
        "\u25aa",  # ▪
        "\u25ab",  # ▫
        "\u25fc",  # ◼
        "\u25fe",  # ◾
        "\u25fd",  # ◽
        "\u25ff",  # ◿
        "\u25a2",  # ▢
        "\u25a3",  # ▣
        "\u25a4",  # ▤
        "\u25a5",  # ▥
        "\u25a6",  # ▦
        "\u25a7",  # ▧
        "\u25a8",  # ▨
        "\u25a9",  # ▩
    }
)


# ---------------------------------------------------------------------------
# Emoji regex — a practical approximation of ``pipeline/processor.py``'s
# ``find_first_emoji`` (line 85) and ``strip_leading_emoji`` (line 108).
# The legacy implementation uses the third-party ``regex`` package with
# ``\p{Emoji}`` property classes; here we match the common Unicode emoji
# ranges directly so the backend can rely on stdlib ``re``. Matches a base
# pictographic glyph plus optional variation selectors, skin-tone
# modifiers, and ZWJ sequences.
# ---------------------------------------------------------------------------
_EMOJI_BASE = (
    r"(?:"
    r"[\U0001F1E6-\U0001F1FF]{2}"  # regional indicator pairs (flags)
    r"|[\U0001F300-\U0001F5FF]"  # misc symbols and pictographs
    r"|[\U0001F600-\U0001F64F]"  # emoticons
    r"|[\U0001F680-\U0001F6FF]"  # transport and map
    r"|[\U0001F700-\U0001F77F]"  # alchemical
    r"|[\U0001F780-\U0001F7FF]"  # geometric shapes extended
    r"|[\U0001F800-\U0001F8FF]"  # supplemental arrows-C
    r"|[\U0001F900-\U0001F9FF]"  # supplemental symbols and pictographs
    r"|[\U0001FA00-\U0001FA6F]"  # chess, symbols and pictographs extended-A
    r"|[\U0001FA70-\U0001FAFF]"  # symbols and pictographs extended-B
    r"|[\u2600-\u26FF]"  # miscellaneous symbols
    r"|[\u2700-\u27BF]"  # dingbats
    r"|[\u25A0-\u25FF]"  # geometric shapes
    r"|[\u2B00-\u2BFF]"  # miscellaneous symbols and arrows
    r")"
)
_EMOJI_MODIFIER = r"[\U0001F3FB-\U0001F3FF]"  # skin tone modifiers
_EMOJI_CLUSTER = (
    rf"{_EMOJI_BASE}[\uFE0E\uFE0F]?(?:{_EMOJI_MODIFIER})?"
    rf"(?:\u200D{_EMOJI_BASE}[\uFE0E\uFE0F]?(?:{_EMOJI_MODIFIER})?)*"
)
_EMOJI_RE: re.Pattern[str] = re.compile(_EMOJI_CLUSTER)


def extract_emoji(text: str) -> tuple[str | None, str]:
    """Return the first non-blocked emoji and the text with it stripped.

    Scans ``text`` for emoji clusters. The first cluster that is not in
    :data:`BLOCKED_EMOJI` is returned together with ``text`` with that
    leading emoji (plus any adjacent whitespace) removed. If no
    acceptable emoji is found, returns ``(None, text)`` unchanged.
    """
    if not text:
        return (None, text)

    for match in _EMOJI_RE.finditer(text):
        candidate = match.group(0)
        if candidate in BLOCKED_EMOJI:
            continue
        # Everything up to and including the matched emoji, plus any
        # following whitespace, is stripped. Any leading content that
        # isn't whitespace or a (blocked) emoji means the emoji sits
        # mid-string and we return the original text unchanged.
        prefix = text[: match.start()]
        prefix_cleaned = _EMOJI_RE.sub("", prefix)
        if prefix_cleaned.strip() != "":
            return (candidate, text)
        stripped = text[match.end() :].lstrip()
        return (candidate, stripped)

    return (None, text)


# ---------------------------------------------------------------------------
# Short name generation — ports the ``Exhibition: `` prefix and
# `` - at {venue}`` suffix handling from ``pipeline/processor.py``'s
# ``create_short_name`` (line 167). The full legacy implementation also
# strips dates, times, and other suffixes; those are intentionally out
# of scope for this PR.
# ---------------------------------------------------------------------------
_EXHIBITION_PREFIX_RE = re.compile(r"^Exhibition:\s*")


def generate_short_name(name: str, location_name: str | None = None) -> str:
    """Return a shortened version of an event ``name``.

    - Strips a leading ``"Exhibition: "`` prefix (case-sensitive).
    - If ``location_name`` is provided, strips a trailing
      ``" - at {location_name}"`` suffix.
    - Idempotent: already-short names are returned unchanged (aside from
      whitespace trimming).
    """
    if not name:
        return name

    short = _EXHIBITION_PREFIX_RE.sub("", name)

    if location_name:
        suffix = f" - at {location_name}"
        if short.endswith(suffix):
            short = short[: -len(suffix)]

    return short.strip()


# ---------------------------------------------------------------------------
# Tag processing — ported from ``pipeline/processor.py`` (lines 262, 336).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagRules:
    """Bucketed tag rules keyed by normalized pattern.

    - ``rewrites``: normalized pattern -> replacement string.
    - ``excludes``: normalized patterns whose tags are dropped.
    - ``removals``: normalized patterns whose presence means "skip event".
    """

    rewrites: dict[str, str]
    excludes: frozenset[str]
    removals: frozenset[str]


def _normalize_tag_key(tag: str) -> str:
    """Normalize a tag for rewrite/exclude/remove lookups."""
    return tag.lower().replace(" ", "")


def _normalize_location_name(name: str | None) -> str:
    """Normalize a location name for matching.

    Ported from ``pipeline/processor.py:586``. Lowercases, strips
    punctuation, drops leading ``"the "`` on longer names, and treats
    ``virtual``/``online``/``livestream`` as non-matches.
    """
    if not name:
        return ""

    original_lower = name.lower()
    normalized = re.sub(r"[^\w\s]", "", original_lower)

    if normalized in {"virtual", "online", "livestream"}:
        return ""
    if len(normalized) > 15 and normalized.startswith("the "):
        normalized = normalized[4:]

    return " ".join(normalized.split())


def _normalize_street_address(addr: str | None) -> str | None:
    """Normalize a street address for matching.

    Ported from ``pipeline/processor.py:639``. Returns ``None`` if the
    input is ``None``/empty or shorter than 5 characters after
    normalization.
    """
    if not addr:
        return None

    normalized = addr.lower().strip()
    replacements = [
        ("avenue", "ave"),
        ("street", "st"),
        ("boulevard", "blvd"),
        ("drive", "dr"),
        ("road", "rd"),
        ("place", "pl"),
        ("court", "ct"),
        ("lane", "ln"),
        ("parkway", "pkwy"),
        ("highway", "hwy"),
        ("east", "e"),
        ("west", "w"),
        ("north", "n"),
        ("south", "s"),
    ]
    for long_form, short_form in replacements:
        normalized = re.sub(r"\b" + long_form + r"\b", short_form, normalized)

    return normalized if len(normalized) >= 5 else None


async def load_tag_rules(db: AsyncSession) -> TagRules:
    """Load active tag rules from the DB, bucketed by ``TagRuleType``."""
    stmt = select(TagRule).where(TagRule.deleted_at.is_(None))
    result = await db.execute(stmt)
    rules = result.scalars().all()

    rewrites: dict[str, str] = {}
    excludes: set[str] = set()
    removals: set[str] = set()

    for rule in rules:
        key = _normalize_tag_key(rule.pattern)
        if rule.rule_type == TagRuleType.rewrite:
            if rule.replacement is not None:
                rewrites[key] = rule.replacement
        elif rule.rule_type == TagRuleType.exclude:
            excludes.add(key)
        elif rule.rule_type == TagRuleType.remove:
            removals.add(key)

    return TagRules(
        rewrites=rewrites,
        excludes=frozenset(excludes),
        removals=frozenset(removals),
    )


def _coerce_raw_tags(raw_tags: list[str] | str | None) -> list[str]:
    """Coerce the raw tag payload (list, hash-delimited string, or None)."""
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        return [tag.strip() for tag in raw_tags if tag and tag.strip()]
    return [
        tag.strip().rstrip(",") for tag in raw_tags.split("#") if tag and tag.strip()
    ]


async def process_tags(
    raw_tags: list[str] | str | None,
    rules: TagRules,
    *,
    extra_tags: list[str] | None = None,
) -> list[str]:
    """Normalize, rewrite, and de-duplicate tags.

    Ported from ``pipeline/processor.py:262`` (simplified). Applies
    rewrite rules, drops tags in ``rules.excludes``, appends
    ``extra_tags``, and de-duplicates while preserving order.
    """
    tags = _coerce_raw_tags(raw_tags)
    processed: list[str] = []
    seen: set[str] = set()

    if extra_tags:
        for tag in extra_tags:
            key = _normalize_tag_key(tag)
            if not key or key in rules.excludes or key in seen:
                continue
            processed.append(tag)
            seen.add(key)

    for tag in tags:
        lookup = _normalize_tag_key(tag)
        final = rules.rewrites.get(lookup, tag)
        final_key = _normalize_tag_key(final)
        if not final_key or final_key in rules.excludes or final_key in seen:
            continue
        processed.append(final)
        seen.add(final_key)

    return processed


def should_skip_for_tags(tags: list[str], rules: TagRules) -> bool:
    """Return ``True`` iff any tag matches a removal rule.

    Ported from ``pipeline/processor.py:336`` (``filter_by_tag``). The
    legacy helper returned ``True`` when the event should be *kept*; this
    inverts the sense to match the function name.
    """
    normalized = {_normalize_tag_key(tag) for tag in tags}
    return not normalized.isdisjoint(rules.removals)


# ---------------------------------------------------------------------------
# Location resolution — ported from ``pipeline/processor.py:769``.
# ---------------------------------------------------------------------------

_FUZZY_THRESHOLD = 0.85
_DEFAULT_LOCATION_EMOJI = "\U0001f3ad"  # 🎭


async def resolve_location(
    db: AsyncSession,
    *,
    location_name: str | None,
    sublocation: str | None,
    source_site_name: str,
    event_name: str,
) -> Location | None:
    """Resolve a ``Location`` for an extracted event.

    Matching order:
      1. Exact match on normalized ``Location.name``.
      2. Exact match on any normalized ``LocationAlternateName``.
      3. Fuzzy match via :class:`difflib.SequenceMatcher` with ratio
         >= 0.85 against normalized names.
      4. Fallback exact match on ``source_site_name`` or ``event_name``.

    If no match is found, a new :class:`Location` is created, flushed to
    obtain its ``id``, and a :func:`geocode_location` Celery task is
    enqueued. The caller owns the transaction: this function never
    commits.
    """
    if not location_name:
        return None

    normalized_loc = _normalize_location_name(location_name)
    normalized_sub = _normalize_location_name(sublocation)
    normalized_event = _normalize_location_name(event_name)
    normalized_site = _normalize_location_name(source_site_name)

    full_loc = f"{normalized_loc} {normalized_sub}".strip()

    # Step 0 (fast dedup path): DB-level exact match on normalized name with
    # optional address tiebreaker. Catches the common case where two extractions
    # produce the same location string without loading all rows into memory.
    # See spec: geocoding-dedup.
    dedup_stmt = select(Location).where(
        func.lower(Location.name) == location_name.lower()
    )
    if sublocation:
        dedup_stmt = dedup_stmt.where(
            or_(
                func.lower(Location.address) == sublocation.lower(),
                Location.address.is_(None),
            )
        )
    dedup_match = (await db.execute(dedup_stmt)).scalar_one_or_none()
    if dedup_match is not None:
        return dedup_match

    stmt = select(Location).options(selectinload(Location.alternate_names))
    result = await db.execute(stmt)
    locations = list(result.scalars().all())

    search_keys = [k for k in (normalized_loc, full_loc) if k]

    # Step 1: exact match on normalized Location.name
    for loc in locations:
        if _normalize_location_name(loc.name) in search_keys:
            return loc

    # Step 2: exact match on any normalized alternate name
    for loc in locations:
        for alt in loc.alternate_names:
            if _normalize_location_name(alt.alternate_name) in search_keys:
                return loc

    # Step 3: fuzzy match via SequenceMatcher ratio >= threshold
    best_loc: Location | None = None
    best_score = 0.0
    for loc in locations:
        candidates = [_normalize_location_name(loc.name)] + [
            _normalize_location_name(alt.alternate_name) for alt in loc.alternate_names
        ]
        for candidate in candidates:
            if not candidate:
                continue
            for key in search_keys:
                if not key:
                    continue
                score = SequenceMatcher(None, candidate, key).ratio()
                if score >= _FUZZY_THRESHOLD and score > best_score:
                    best_loc = loc
                    best_score = score
    if best_loc is not None:
        return best_loc

    # Step 4: fallback on source_site_name / event_name exact normalized match
    fallback_keys = [k for k in (normalized_site, normalized_event) if k]
    if fallback_keys:
        for loc in locations:
            if _normalize_location_name(loc.name) in fallback_keys:
                return loc
            for alt in loc.alternate_names:
                if _normalize_location_name(alt.alternate_name) in fallback_keys:
                    return loc

    # No match — create a new Location and enqueue geocoding.
    new_loc = Location(
        name=location_name,
        address=sublocation or None,
        emoji=_DEFAULT_LOCATION_EMOJI,
    )
    db.add(new_loc)
    await db.flush()
    try:
        geocode_location.delay(new_loc.id)
    except Exception as exc:
        # If Celery/Redis is unavailable (e.g. broker not deployed in dev),
        # log and continue. The new location is still persisted with
        # NULL coordinates; operators can run /backfill-geocode later or
        # edit coordinates manually via PUT /locations/{id}.
        logger.warning(
            "Could not enqueue geocoding for new location %d (%s): %s. "
            "Location was created without coordinates; run backfill-geocode later.",
            new_loc.id,
            location_name,
            exc,
        )
    return new_loc
