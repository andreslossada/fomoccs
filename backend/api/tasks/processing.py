"""Celery task for processing extracted CrawlResults into events."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from api.celery_app import celery
from api.config import get_settings
from api.models.base import CrawlResultStatus
from api.models.crawl import CrawlResult, ExtractedEvent
from api.services.event_merging import merge_extracted_events
from api.services.event_processing import (
    extract_emoji,
    generate_short_name,
    load_tag_rules,
    process_tags,
    resolve_location,
    should_skip_for_tags,
)
from api.task_names import PROCESS_CRAWL_JOB

logger = logging.getLogger(__name__)


def _make_session() -> async_sessionmaker[AsyncSession]:
    """Create a fresh engine + session factory bound to the current event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _process_crawl_job(job_id: int) -> None:
    """Load all extracted CrawlResults for job_id and process each source."""
    session_factory = _make_session()

    async with session_factory() as session:
        stmt = (
            select(CrawlResult)
            .where(
                CrawlResult.crawl_job_id == job_id,
                CrawlResult.status == CrawlResultStatus.extracted,
            )
            .options(
                selectinload(CrawlResult.extracted_events),
                selectinload(CrawlResult.source),
            )
        )
        result = await session.execute(stmt)
        crawl_results = list(result.scalars().all())

    if not crawl_results:
        logger.warning(
            "No extracted CrawlResults found for job_id=%d, nothing to process",
            job_id,
        )
        return

    logger.info(
        "Processing %d CrawlResult(s) for job_id=%d",
        len(crawl_results),
        job_id,
    )

    for crawl_result in crawl_results:
        await _process_single_crawl_result(session_factory, crawl_result.id, job_id)


async def _process_single_crawl_result(
    session_factory: async_sessionmaker[AsyncSession],
    crawl_result_id: int,
    job_id: int,
) -> None:
    """Process one CrawlResult; isolates failures so others continue."""
    async with session_factory() as session:
        try:
            crawl_result = await session.scalar(
                select(CrawlResult)
                .where(CrawlResult.id == crawl_result_id)
                .options(
                    selectinload(CrawlResult.extracted_events),
                    selectinload(CrawlResult.source),
                )
            )
            if crawl_result is None:
                logger.warning("CrawlResult %d not found, skipping", crawl_result_id)
                return

            tag_rules = await load_tag_rules(session)

            extracted_events: list[ExtractedEvent] = list(crawl_result.extracted_events)
            source_name: str = (
                crawl_result.source.name
                if crawl_result.source is not None
                else f"source_{crawl_result.source_id}"
            )

            logger.info(
                "Processing %d event(s) for CrawlResult %d (source: %s)",
                len(extracted_events),
                crawl_result_id,
                source_name,
            )

            for event in extracted_events:
                location = await resolve_location(
                    session,
                    location_name=event.location_name,
                    sublocation=event.sublocation,
                    source_site_name=source_name,
                    event_name=event.name,
                )
                event.location_id = location.id if location is not None else None

                raw_tags: list[str] | str | None = event.tags  # type: ignore[assignment]
                tags = await process_tags(raw_tags, tag_rules)

                if should_skip_for_tags(tags, tag_rules):
                    logger.debug("Skipping event %d due to tag removal rules", event.id)
                    continue

                short_name = generate_short_name(
                    event.name,
                    location_name=location.name if location is not None else None,
                )
                event.short_name = short_name

                emoji, _ = extract_emoji(event.name)
                if emoji is not None:
                    event.emoji = emoji

            await session.flush()

            await merge_extracted_events(session, crawl_job_id=job_id)

            crawl_result.status = CrawlResultStatus.processed
            crawl_result.processed_at = datetime.utcnow()
            await session.commit()

            logger.info(
                "CrawlResult %d (source: %s) processed successfully",
                crawl_result_id,
                source_name,
            )

        except Exception as exc:
            await session.rollback()

            # Re-open a fresh connection to mark the result as failed
            async with session_factory() as fail_session:
                failed_result = await fail_session.scalar(
                    select(CrawlResult).where(CrawlResult.id == crawl_result_id)
                )
                if failed_result is not None:
                    failed_result.status = CrawlResultStatus.failed
                    failed_result.error_message = str(exc)
                    await fail_session.commit()

            logger.error(
                "CrawlResult %d failed during processing: %s",
                crawl_result_id,
                exc,
                exc_info=True,
            )


@celery.task(bind=True, name=PROCESS_CRAWL_JOB)
def process_crawl_job(self: Any, job_id: int) -> None:
    """Process all extracted CrawlResults for a crawl job.

    Runs the full backend event-processing pipeline (location resolution,
    tag processing, short name, emoji, dedup/merge/archive) for each
    extracted CrawlResult. Each source is isolated: a failure marks only
    that CrawlResult as failed and processing continues for the rest.
    """
    asyncio.run(_process_crawl_job(job_id))
