"""Integration tests for the process_crawl_job pipeline task."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.base import CrawlJobStatus, CrawlResultStatus, TagRuleType
from api.models.crawl import CrawlJob, CrawlResult, ExtractedEvent
from api.models.source import Source
from api.models.tag import TagRule
from api.tasks.processing import _process_crawl_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_source(session: AsyncSession, name: str = "Test Source") -> Source:
    from api.models.base import SourceType

    source = Source(name=name, type=SourceType.crawler)
    session.add(source)
    await session.flush()
    return source


async def _make_job(session: AsyncSession) -> CrawlJob:
    job = CrawlJob(status=CrawlJobStatus.running)
    session.add(job)
    await session.flush()
    return job


async def _make_crawl_result(
    session: AsyncSession, job: CrawlJob, source: Source
) -> CrawlResult:
    cr = CrawlResult(
        crawl_job_id=job.id,
        source_id=source.id,
        status=CrawlResultStatus.extracted,
    )
    session.add(cr)
    await session.flush()
    return cr


async def _make_extracted_event(
    session: AsyncSession,
    crawl_result: CrawlResult,
    name: str,
    location_name: str | None = "Test Venue",
    tags: list[str] | None = None,
) -> ExtractedEvent:
    ev = ExtractedEvent(
        crawl_result_id=crawl_result.id,
        name=name,
        location_name=location_name,
        tags=tags,
    )
    session.add(ev)
    await session.flush()
    return ev


class _NoCommitSession:
    """Proxy that delegates all attribute access to the real AsyncSession
    but turns commit() into flush() and rollback() into a no-op so the
    test transaction is never closed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def commit(self) -> None:
        await self._session.flush()

    async def rollback(self) -> None:
        pass  # keep test transaction alive


class _SessionStub:
    def __init__(self, session: AsyncSession) -> None:
        self._proxy = _NoCommitSession(session)

    async def __aenter__(self) -> _NoCommitSession:
        return self._proxy

    async def __aexit__(self, *args: object) -> None:
        pass


class SessionFactoryStub:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionStub:
        return _SessionStub(self._session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcessCrawlJobHappyPath:
    @pytest.mark.asyncio
    async def test_marks_result_processed_and_enqueues_geocoding(
        self, db_session: AsyncSession
    ) -> None:
        """Happy path: extracted events processed, new locations geocoded."""
        job = await _make_job(db_session)
        source = await _make_source(db_session)
        cr = await _make_crawl_result(db_session, job, source)
        await _make_extracted_event(
            db_session,
            cr,
            "\U0001f3a8 Exhibition: Art Show",
            location_name="New Gallery",
        )
        await _make_extracted_event(
            db_session, cr, "Jazz Night", location_name="New Gallery"
        )
        await db_session.flush()

        stub = SessionFactoryStub(db_session)

        with (
            patch("api.tasks.processing._make_session", return_value=stub),
            patch("api.services.event_processing.geocode_location") as mock_geo,
            patch(
                "api.tasks.processing.merge_extracted_events",
                new_callable=AsyncMock,
            ) as mock_merge,
        ):
            mock_geo.delay = MagicMock()
            await _process_crawl_job(job.id)

        await db_session.refresh(cr)
        assert cr.status == CrawlResultStatus.processed
        assert cr.error_message is None
        mock_merge.assert_called_once()
        # New location created once; second event reuses same location
        mock_geo.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_on_removal_tag_does_not_prevent_processed_status(
        self, db_session: AsyncSession
    ) -> None:
        """Events with removal-tagged tags are skipped; result still processed."""
        job = await _make_job(db_session)
        source = await _make_source(db_session)
        cr = await _make_crawl_result(db_session, job, source)

        rule = TagRule(rule_type=TagRuleType.remove, pattern="skip-me")
        db_session.add(rule)
        await db_session.flush()

        await _make_extracted_event(
            db_session, cr, "Skipped Event", location_name=None, tags=["skip-me"]
        )
        await _make_extracted_event(
            db_session, cr, "Normal Event", location_name="A Venue"
        )
        await db_session.flush()

        stub = SessionFactoryStub(db_session)

        with (
            patch("api.tasks.processing._make_session", return_value=stub),
            patch("api.services.event_processing.geocode_location") as mock_geo,
            patch(
                "api.tasks.processing.merge_extracted_events",
                new_callable=AsyncMock,
            ),
        ):
            mock_geo.delay = MagicMock()
            await _process_crawl_job(job.id)

        await db_session.refresh(cr)
        assert cr.status == CrawlResultStatus.processed
        # Skipped event has no location_name so no geocoding; normal event gets 1 call
        assert mock_geo.delay.call_count == 1


class TestProcessCrawlJobIsolation:
    @pytest.mark.asyncio
    async def test_one_source_fails_other_succeeds(
        self, db_session: AsyncSession
    ) -> None:
        """Per-source isolation: failure in one result leaves other processed."""
        job = await _make_job(db_session)
        source_a = await _make_source(db_session, "Source A")
        source_b = await _make_source(db_session, "Source B")

        cr_a = await _make_crawl_result(db_session, job, source_a)
        cr_b = await _make_crawl_result(db_session, job, source_b)

        await _make_extracted_event(
            db_session, cr_a, "Event A", location_name="Venue A"
        )
        await _make_extracted_event(
            db_session, cr_b, "Event B", location_name="Venue B"
        )
        await db_session.flush()

        cr_a_id = cr_a.id
        cr_b_id = cr_b.id

        stub = SessionFactoryStub(db_session)
        call_count = 0

        async def fake_merge(session: Any, *, crawl_job_id: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated merge failure")

        with (
            patch("api.tasks.processing._make_session", return_value=stub),
            patch("api.services.event_processing.geocode_location") as mock_geo,
            patch(
                "api.tasks.processing.merge_extracted_events",
                side_effect=fake_merge,
            ),
        ):
            mock_geo.delay = MagicMock()
            await _process_crawl_job(job.id)

        cr_a_fresh = await db_session.get(CrawlResult, cr_a_id)
        cr_b_fresh = await db_session.get(CrawlResult, cr_b_id)

        assert cr_a_fresh is not None
        assert cr_b_fresh is not None

        statuses = {cr_a_fresh.status, cr_b_fresh.status}
        assert CrawlResultStatus.failed in statuses
        assert CrawlResultStatus.processed in statuses

        failed = (
            cr_a_fresh if cr_a_fresh.status == CrawlResultStatus.failed else cr_b_fresh
        )
        assert failed.error_message is not None
        assert "simulated merge failure" in failed.error_message


class TestProcessCrawlJobEmptyJob:
    @pytest.mark.asyncio
    async def test_empty_job_logs_warning_and_completes(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Job with no extracted CrawlResults completes without error."""
        job = await _make_job(db_session)
        await db_session.flush()

        stub = SessionFactoryStub(db_session)

        with (
            patch("api.tasks.processing._make_session", return_value=stub),
            caplog.at_level(logging.WARNING, logger="api.tasks.processing"),
        ):
            await _process_crawl_job(job.id)

        assert any(
            "No extracted CrawlResults" in record.message for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_short_name_and_emoji_extracted(
        self, db_session: AsyncSession
    ) -> None:
        """Short_name has Exhibition prefix stripped; emoji is extracted."""
        job = await _make_job(db_session)
        source = await _make_source(db_session, "Gallery Source")
        cr = await _make_crawl_result(db_session, job, source)

        ev = await _make_extracted_event(
            db_session,
            cr,
            "Exhibition: \U0001f3a8 Abstract Works",
            location_name="City Gallery",
        )
        ev_id = ev.id
        await db_session.flush()

        stub = SessionFactoryStub(db_session)

        with (
            patch("api.tasks.processing._make_session", return_value=stub),
            patch("api.services.event_processing.geocode_location") as mock_geo,
            patch(
                "api.tasks.processing.merge_extracted_events",
                new_callable=AsyncMock,
            ),
        ):
            mock_geo.delay = MagicMock()
            await _process_crawl_job(job.id)

        refreshed_ev = await db_session.get(ExtractedEvent, ev_id)
        assert refreshed_ev is not None
        assert refreshed_ev.short_name is not None
        assert "Exhibition:" not in refreshed_ev.short_name
        assert refreshed_ev.emoji == "\U0001f3a8"

        stmt = select(CrawlResult).where(CrawlResult.id == cr.id)
        result = await db_session.execute(stmt)
        cr_fresh = result.scalar_one()
        assert cr_fresh.status == CrawlResultStatus.processed
