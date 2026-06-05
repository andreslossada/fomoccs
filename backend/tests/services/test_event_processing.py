"""Tests for event-processing helpers (pure + async DB services)."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.base import TagRuleType
from api.models.location import Location, LocationAlternateName
from api.models.tag import TagRule
from api.services.event_processing import (
    BLOCKED_EMOJI,
    TagRules,
    extract_emoji,
    generate_short_name,
    load_tag_rules,
    process_tags,
    resolve_location,
    should_skip_for_tags,
)


def test_generate_short_name_strips_exhibition_prefix() -> None:
    assert generate_short_name("Exhibition: Monet") == "Monet"


def test_generate_short_name_strips_at_venue_suffix() -> None:
    assert generate_short_name("Monet - at MoMA", "MoMA") == "Monet"


def test_generate_short_name_leaves_short_name_untouched() -> None:
    assert generate_short_name("Monet") == "Monet"


def test_extract_emoji_returns_first_emoji_and_stripped_text() -> None:
    assert extract_emoji("\U0001f3a8 Art Show") == ("\U0001f3a8", "Art Show")


def test_extract_emoji_none_when_no_emoji() -> None:
    assert extract_emoji("Art Show") == (None, "Art Show")


def test_extract_emoji_skips_blocked_emoji_with_no_next() -> None:
    blocked = "\u25aa"
    assert blocked in BLOCKED_EMOJI

    text = f"{blocked} Art Show"
    assert extract_emoji(text) == (None, text)


def test_extract_emoji_skips_blocked_emoji_and_finds_next() -> None:
    blocked = "\u25aa"
    text = f"{blocked} \U0001f3a8 Art Show"
    emoji, stripped = extract_emoji(text)
    assert emoji == "\U0001f3a8"
    assert stripped == "Art Show"


async def _cleanup_tag_rules(db_session: AsyncSession) -> None:
    """Remove any pre-existing TagRule rows so each test is isolated."""
    rows = (await db_session.execute(select(TagRule))).scalars().all()
    for row in rows:
        await db_session.delete(row)
    await db_session.flush()


@pytest.mark.asyncio
async def test_resolve_location_exact_name_match(db_session: AsyncSession) -> None:
    existing = Location(name="Museum of Modern Art", emoji="\U0001f3a8")
    db_session.add(existing)
    await db_session.flush()

    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Museum of Modern Art",
            sublocation=None,
            source_site_name="site",
            event_name="Some Event",
        )

    assert result is not None
    assert result.id == existing.id
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_alternate_name_match(db_session: AsyncSession) -> None:
    loc = Location(name="Museum of Modern Art", emoji="\U0001f3a8")
    loc.alternate_names = [LocationAlternateName(alternate_name="MoMA")]
    db_session.add(loc)
    await db_session.flush()

    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name="MoMA",
            sublocation=None,
            source_site_name="site",
            event_name="Show",
        )

    assert result is not None
    assert result.id == loc.id
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_fuzzy_match_above_threshold(
    db_session: AsyncSession,
) -> None:
    loc = Location(name="Museum of Modern Art", emoji="\U0001f3a8")
    db_session.add(loc)
    await db_session.flush()

    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Museum of Moderne Art",
            sublocation=None,
            source_site_name="site",
            event_name="Show",
        )

    assert result is not None
    assert result.id == loc.id
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_unknown_creates_and_enqueues_geocode(
    db_session: AsyncSession,
) -> None:
    with patch(
        "api.services.event_processing.geocode_location", new=MagicMock()
    ) as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Totally Unique Venue 42",
            sublocation=None,
            source_site_name="some-source",
            event_name="Mystery Event",
        )

    assert result is not None
    assert result.id is not None
    assert result.name == "Totally Unique Venue 42"
    assert result.emoji == "\U0001f3ad"
    mock_task.delay.assert_called_once_with(result.id)

    fetched = await db_session.get(Location, result.id)
    assert fetched is not None
    assert fetched.name == "Totally Unique Venue 42"


@pytest.mark.asyncio
async def test_resolve_location_none_location_name_returns_none(
    db_session: AsyncSession,
) -> None:
    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name=None,
            sublocation=None,
            source_site_name="site",
            event_name="Show",
        )

    assert result is None
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_dedup_hits_when_sublocation_matches(
    db_session: AsyncSession,
) -> None:
    """Dedup fast-path: same name + same sublocation returns existing Location."""
    existing = Location(
        name="Centro Cultural Chacao", address="Sala 1", emoji="\U0001f3ad"
    )
    db_session.add(existing)
    await db_session.flush()

    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Centro Cultural Chacao",
            sublocation="Sala 1",
            source_site_name="site",
            event_name="Some Show",
        )

    assert result is not None
    assert result.id == existing.id
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_dedup_falls_back_to_null_address(
    db_session: AsyncSession,
) -> None:
    """Dedup fast-path: when DB row has NULL address, any sublocation matches."""
    existing = Location(name="Centro Cultural Chacao", address=None, emoji="\U0001f3ad")
    db_session.add(existing)
    await db_session.flush()

    with patch("api.services.event_processing.geocode_location") as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Centro Cultural Chacao",
            sublocation="Sala 2",
            source_site_name="site",
            event_name="Some Show",
        )

    assert result is not None
    assert result.id == existing.id
    mock_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_location_dedup_misses_when_sublocation_differs(
    db_session: AsyncSession,
) -> None:
    """Dedup fast-path misses when DB row has a different sublocation.

    The function then falls through to the in-memory scan. Here no in-memory
    match exists either, so a new Location is created.
    """
    existing = Location(
        name="Centro Cultural Chacao", address="Sala 1", emoji="\U0001f3ad"
    )
    db_session.add(existing)
    await db_session.flush()

    with patch(
        "api.services.event_processing.geocode_location", new=MagicMock()
    ) as mock_task:
        result = await resolve_location(
            db_session,
            location_name="Centro Cultural Chacao",
            sublocation="Sala 7",
            source_site_name="site",
            event_name="Some Show",
        )

    assert result is not None
    assert result.id != existing.id
    assert result.address == "Sala 7"
    mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_process_tags_rewrite_transforms(db_session: AsyncSession) -> None:
    await _cleanup_tag_rules(db_session)
    db_session.add(
        TagRule(
            rule_type=TagRuleType.rewrite,
            pattern="livemusic",
            replacement="Live Music",
        )
    )
    await db_session.flush()

    rules = await load_tag_rules(db_session)
    result = await process_tags(["LiveMusic"], rules)

    assert result == ["Live Music"]


@pytest.mark.asyncio
async def test_process_tags_exclude_drops(db_session: AsyncSession) -> None:
    await _cleanup_tag_rules(db_session)
    db_session.add(
        TagRule(
            rule_type=TagRuleType.exclude,
            pattern="spam",
            replacement=None,
        )
    )
    await db_session.flush()

    rules = await load_tag_rules(db_session)
    result = await process_tags(["Spam", "Art"], rules)

    assert result == ["Art"]


@pytest.mark.asyncio
async def test_process_tags_extra_tags_appended_and_deduped(
    db_session: AsyncSession,
) -> None:
    await _cleanup_tag_rules(db_session)
    rules = await load_tag_rules(db_session)

    result = await process_tags(["Art"], rules, extra_tags=["art"])

    assert len(result) == 1
    assert result[0].lower() == "art"


@pytest.mark.asyncio
async def test_should_skip_for_tags_removal(db_session: AsyncSession) -> None:
    await _cleanup_tag_rules(db_session)
    db_session.add(
        TagRule(
            rule_type=TagRuleType.remove,
            pattern="nsfw",
            replacement=None,
        )
    )
    await db_session.flush()

    rules = await load_tag_rules(db_session)
    assert should_skip_for_tags(["NSFW", "Art"], rules) is True


@pytest.mark.asyncio
async def test_should_skip_for_tags_false(db_session: AsyncSession) -> None:
    await _cleanup_tag_rules(db_session)
    rules = await load_tag_rules(db_session)

    assert should_skip_for_tags(["Art", "Music"], rules) is False
    empty_rules = TagRules(rewrites={}, excludes=frozenset(), removals=frozenset())
    assert should_skip_for_tags(["nsfw"], empty_rules) is False
