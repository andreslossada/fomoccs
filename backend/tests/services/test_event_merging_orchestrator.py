"""End-to-end tests for the :func:`merge_extracted_events` orchestrator."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.base import (
    CrawlJobStatus,
    CrawlResultStatus,
    EventStatus,
    ExtractedEventStatus,
    SourceType,
)
from api.models.crawl import (
    CrawlJob,
    CrawlResult,
    ExtractedEvent,
    ExtractedEventLog,
)
from api.models.event import (
    Event,
    EventOccurrence,
    EventSource,
    EventUrl,
)
from api.models.location import Location
from api.models.source import Source
from api.services.event_merging import (
    MergeResult,
    merge_extracted_events,
)

TODAY = date(2026, 4, 10)


async def _make_source(db: AsyncSession, *, name: str = "Src") -> Source:
    source = Source(name=name, type=SourceType.crawler)
    db.add(source)
    await db.flush()
    return source


async def _make_location(db: AsyncSession, *, name: str = "Venue") -> Location:
    loc = Location(name=name, emoji="\U0001f3a8")
    db.add(loc)
    await db.flush()
    return loc


async def _make_processed_crawl_result(
    db: AsyncSession, *, source: Source
) -> CrawlResult:
    job = CrawlJob(status=CrawlJobStatus.completed)
    db.add(job)
    await db.flush()
    cr = CrawlResult(
        crawl_job_id=job.id,
        source_id=source.id,
        status=CrawlResultStatus.processed,
    )
    db.add(cr)
    await db.flush()
    return cr


async def _make_extracted_event(
    db: AsyncSession,
    *,
    crawl_result: CrawlResult,
    name: str,
    location: Location | None,
    occurrences: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    url: str | None = "https://example.com/e",
) -> ExtractedEvent:
    ee = ExtractedEvent(
        crawl_result_id=crawl_result.id,
        name=name,
        location_id=location.id if location else None,
        url=url,
        occurrences=occurrences,
        tags=tags,
    )
    db.add(ee)
    await db.flush()
    return ee


def _future_occ(days: int) -> dict[str, Any]:
    return {"start_date": (TODAY + timedelta(days=days)).isoformat()}


@pytest.mark.asyncio
async def test_merge_creates_new_event_and_logs_created(
    db_session: AsyncSession,
) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)
    cr = await _make_processed_crawl_result(db_session, source=source)
    ee = await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Jazz Night",
        location=location,
        occurrences=[_future_occ(2)],
        tags=["music"],
    )

    result = await merge_extracted_events(db_session, today=TODAY)

    assert isinstance(result, MergeResult)
    assert result.new_events_count == 1
    assert result.merged_count == 0

    event = (
        await db_session.execute(select(Event).where(Event.name == "Jazz Night"))
    ).scalar_one()
    assert event.location_id == location.id
    assert event.status == EventStatus.active

    occ_count = len(
        (
            await db_session.execute(
                select(EventOccurrence).where(EventOccurrence.event_id == event.id)
            )
        )
        .scalars()
        .all()
    )
    assert occ_count == 1

    urls = (
        (
            await db_session.execute(
                select(EventUrl.url).where(EventUrl.event_id == event.id)
            )
        )
        .scalars()
        .all()
    )
    assert urls == ["https://example.com/e"]

    sources = (
        (
            await db_session.execute(
                select(EventSource).where(EventSource.event_id == event.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(sources) == 1
    assert sources[0].is_primary is True
    assert sources[0].extracted_event_id == ee.id

    log = (
        await db_session.execute(
            select(ExtractedEventLog).where(
                ExtractedEventLog.extracted_event_id == ee.id
            )
        )
    ).scalar_one()
    assert log.status == ExtractedEventStatus.created
    assert log.event_id == event.id


@pytest.mark.asyncio
async def test_merge_merges_duplicate_and_logs_merged(
    db_session: AsyncSession,
) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)

    # Pre-existing event with an overlapping future occurrence.
    existing = Event(name="Jazz Night", location_id=location.id)
    db_session.add(existing)
    await db_session.flush()
    db_session.add(
        EventOccurrence(event_id=existing.id, start_date=TODAY + timedelta(days=2))
    )
    # Link existing event to source via a prior crawl_result + extracted_event
    # so the source appears "still seeing" it and it isn't archived.
    prior_cr = await _make_processed_crawl_result(db_session, source=source)
    prior_ee = ExtractedEvent(
        crawl_result_id=prior_cr.id, name="Jazz Night", location_id=location.id
    )
    db_session.add(prior_ee)
    await db_session.flush()
    db_session.add(
        EventSource(
            event_id=existing.id,
            extracted_event_id=prior_ee.id,
            source_id=source.id,
            is_primary=True,
        )
    )
    await db_session.flush()

    # New crawl_result with a duplicate extracted event.
    cr = await _make_processed_crawl_result(db_session, source=source)
    ee = await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Jazz Night",
        location=location,
        occurrences=[_future_occ(2)],
        url="https://example.com/new",
    )

    result = await merge_extracted_events(db_session, today=TODAY)

    assert result.new_events_count == 0
    assert result.merged_count == 1

    sources = (
        (
            await db_session.execute(
                select(EventSource)
                .where(EventSource.event_id == existing.id)
                .order_by(EventSource.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(sources) == 2
    assert sources[1].is_primary is False
    assert sources[1].extracted_event_id == ee.id

    log = (
        await db_session.execute(
            select(ExtractedEventLog).where(
                ExtractedEventLog.extracted_event_id == ee.id
            )
        )
    ).scalar_one()
    assert log.status == ExtractedEventStatus.merged
    assert log.event_id == existing.id


@pytest.mark.asyncio
async def test_merge_skips_missing_location(db_session: AsyncSession) -> None:
    source = await _make_source(db_session)
    cr = await _make_processed_crawl_result(db_session, source=source)
    ee = await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="No Venue",
        location=None,
        occurrences=[_future_occ(1)],
    )

    result = await merge_extracted_events(db_session, today=TODAY)
    assert result.new_events_count == 0
    assert result.merged_count == 0

    log = (
        await db_session.execute(
            select(ExtractedEventLog).where(
                ExtractedEventLog.extracted_event_id == ee.id
            )
        )
    ).scalar_one()
    assert log.status == ExtractedEventStatus.skipped_no_location


@pytest.mark.asyncio
async def test_merge_skips_no_valid_occurrences(db_session: AsyncSession) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)
    cr = await _make_processed_crawl_result(db_session, source=source)
    # All occurrences in the past -> filtered out by parse_occurrences.
    ee = await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Past Event",
        location=location,
        occurrences=[
            {"start_date": (TODAY - timedelta(days=5)).isoformat()},
        ],
    )

    result = await merge_extracted_events(db_session, today=TODAY)
    assert result.new_events_count == 0
    assert result.merged_count == 0

    log = (
        await db_session.execute(
            select(ExtractedEventLog).where(
                ExtractedEventLog.extracted_event_id == ee.id
            )
        )
    ).scalar_one()
    assert log.status == ExtractedEventStatus.skipped_no_occurrences


@pytest.mark.asyncio
async def test_merge_raises_on_missing_source_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fabricate a row with source_id=None by monkeypatching the loader.
    source = await _make_source(db_session)
    location = await _make_location(db_session)
    cr = await _make_processed_crawl_result(db_session, source=source)
    await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Bad Row",
        location=location,
        occurrences=[_future_occ(1)],
    )

    # Patch AsyncSession.execute to mangle the first query result only.
    from api.services import event_merging as em

    original_execute = db_session.execute
    call_count = {"n": 0}

    async def mangled_execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original_execute(stmt, *args, **kwargs)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Replace the first (unprocessed) query's rows with source_id=None.
            rows = result.all()
            # Column 8 is CrawlResult.source_id. Splicing it out of the
            # row keeps this from going stale when the query gains a column.
            mangled = [(*r[:8], None, *r[9:]) for r in rows]

            class _FakeResult:
                def all(self_inner) -> list[Any]:
                    return mangled

            return _FakeResult()
        return result

    monkeypatch.setattr(db_session, "execute", mangled_execute)

    with pytest.raises(RuntimeError, match="no source_id"):
        await em.merge_extracted_events(db_session, today=TODAY)


@pytest.mark.asyncio
async def test_merge_within_batch_dedup(db_session: AsyncSession) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)
    cr = await _make_processed_crawl_result(db_session, source=source)

    await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Repeat Concert",
        location=location,
        occurrences=[_future_occ(3)],
        url="https://example.com/a",
    )
    await _make_extracted_event(
        db_session,
        crawl_result=cr,
        name="Repeat Concert",
        location=location,
        occurrences=[_future_occ(3)],
        url="https://example.com/b",
    )

    result = await merge_extracted_events(db_session, today=TODAY)

    # Only one new event; the second row is merged into the first.
    assert result.new_events_count == 1
    assert result.merged_count == 1

    events = (
        (await db_session.execute(select(Event).where(Event.name == "Repeat Concert")))
        .scalars()
        .all()
    )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_merge_archives_event_not_seen_in_latest_crawl(
    db_session: AsyncSession,
) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)

    # Old crawl_result with an extracted_event linked to an existing event.
    old_cr = await _make_processed_crawl_result(db_session, source=source)
    old_ee = ExtractedEvent(
        crawl_result_id=old_cr.id, name="Old Show", location_id=location.id
    )
    db_session.add(old_ee)
    await db_session.flush()
    old_event = Event(name="Old Show", location_id=location.id)
    db_session.add(old_event)
    await db_session.flush()
    # Past occurrence only -> no future grace.
    db_session.add(
        EventOccurrence(event_id=old_event.id, start_date=TODAY - timedelta(days=5))
    )
    db_session.add(
        EventSource(
            event_id=old_event.id,
            extracted_event_id=old_ee.id,
            source_id=source.id,
            is_primary=True,
        )
    )
    await db_session.flush()

    # New crawl_result with a fresh unrelated event (so the orchestrator
    # sees this source as "touched"). The old event is NOT in this crawl.
    new_cr = await _make_processed_crawl_result(db_session, source=source)
    await _make_extracted_event(
        db_session,
        crawl_result=new_cr,
        name="Brand New Thing",
        location=location,
        occurrences=[_future_occ(4)],
    )

    result = await merge_extracted_events(db_session, today=TODAY)

    assert result.new_events_count == 1
    assert result.archived_count >= 1

    refreshed = (
        await db_session.execute(select(Event).where(Event.id == old_event.id))
    ).scalar_one()
    assert refreshed.status == EventStatus.archived


@pytest.mark.asyncio
async def test_merge_grace_period_preserves_future_event(
    db_session: AsyncSession,
) -> None:
    source = await _make_source(db_session)
    location = await _make_location(db_session)

    old_cr = await _make_processed_crawl_result(db_session, source=source)
    old_ee = ExtractedEvent(
        crawl_result_id=old_cr.id, name="Far Future Show", location_id=location.id
    )
    db_session.add(old_ee)
    await db_session.flush()
    preserved = Event(name="Far Future Show", location_id=location.id)
    db_session.add(preserved)
    await db_session.flush()
    # Occurrence >= today + 14 days should trigger the grace period.
    db_session.add(
        EventOccurrence(event_id=preserved.id, start_date=TODAY + timedelta(days=30))
    )
    db_session.add(
        EventSource(
            event_id=preserved.id,
            extracted_event_id=old_ee.id,
            source_id=source.id,
            is_primary=True,
        )
    )
    await db_session.flush()

    new_cr = await _make_processed_crawl_result(db_session, source=source)
    await _make_extracted_event(
        db_session,
        crawl_result=new_cr,
        name="Unrelated Other",
        location=location,
        occurrences=[_future_occ(1)],
    )

    result = await merge_extracted_events(db_session, today=TODAY)

    assert result.archived_count == 0
    refreshed = (
        await db_session.execute(select(Event).where(Event.id == preserved.id))
    ).scalar_one()
    assert refreshed.status == EventStatus.active
