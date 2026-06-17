"""Async database helpers for the TUI.

All queries reuse the same async engine and session factory from the API
layer so there is zero duplication of connection config or model definitions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal
from api.models import (
    CrawlJob,
    CrawlResult,
    CrawlSummary,
    Event,
    Location,
    Source,
    TagRule,
    User,
)


async def get_session() -> AsyncSession:
    """Create a new async DB session from the shared pool."""
    return AsyncSessionLocal()


# ============================================================================
# Dashboard stats
# ============================================================================


async def db_ping(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def active_crawl_jobs(session: AsyncSession) -> list[CrawlJob]:
    """Jobs that started in the last 2 hours and are still running."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    result = await session.execute(
        select(CrawlJob)
        .where(
            CrawlJob.status == "running",
            CrawlJob.started_at >= cutoff,
        )
        .order_by(CrawlJob.started_at.desc())
    )
    return list(result.scalars().all())


async def count_stuck_jobs(session: AsyncSession) -> int:
    """Count running jobs older than 2 hours (stuck)."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    result = await session.scalar(
        select(func.count(CrawlJob.id)).where(
            CrawlJob.status == "running",
            CrawlJob.started_at < cutoff,
        )
    )
    return result or 0


async def recent_events_count(session: AsyncSession) -> int:
    cutoff = (
        datetime.now(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .replace(tzinfo=None)
    )
    result = await session.execute(
        select(func.count(Event.id)).where(Event.created_at >= cutoff)
    )
    return result.scalar() or 0


async def sources_summary(session: AsyncSession) -> dict[str, int]:
    total = await session.scalar(
        select(func.count(Source.id)).where(Source.deleted_at.is_(None))
    )
    active = await session.scalar(
        select(func.count(Source.id)).where(
            Source.deleted_at.is_(None), Source.disabled.is_(False)
        )
    )
    return {
        "total": total or 0,
        "active": active or 0,
        "disabled": (total or 0) - (active or 0),
    }


async def llm_usage_today(session: AsyncSession) -> dict[str, Any]:
    cutoff = (
        datetime.now(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .replace(tzinfo=None)
    )
    result = await session.execute(
        select(
            func.coalesce(func.sum(CrawlSummary.api_calls), 0).label("api_calls"),
            func.coalesce(func.sum(CrawlSummary.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(CrawlSummary.output_tokens), 0).label(
                "output_tokens"
            ),
            func.coalesce(func.sum(CrawlSummary.estimated_cost), 0).label("cost"),
        ).where(CrawlSummary.created_at >= cutoff)
    )
    row = result.one()
    return {
        "api_calls": row[0],
        "input_tokens": row[1],
        "output_tokens": row[2],
        "cost": row[3],
    }


async def hourly_llm_cost_last_24h(
    session: AsyncSession,
) -> list[float]:
    """Hourly LLM cost for the last 24 hours (sparkline data)."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    result = await session.execute(
        select(
            func.date_trunc("hour", CrawlSummary.created_at).label("hour"),
            func.coalesce(func.sum(CrawlSummary.estimated_cost), 0).label("cost"),
        )
        .where(CrawlSummary.created_at >= cutoff)
        .group_by("hour")
        .order_by("hour")
    )
    hours: dict[int, float] = {h: 0.0 for h in range(24)}
    now_hour = datetime.now(UTC).hour
    for row in result.all():
        h = row[0].hour
        bucket = (h - now_hour - 1) % 24
        hours[bucket] = float(row[1])
    return [hours[h] for h in range(24)]


async def hourly_events_last_24h(
    session: AsyncSession,
) -> list[int]:
    """Event count per hour for the last 24 hours (sparkline data)."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    result = await session.execute(
        select(
            func.date_trunc("hour", Event.created_at).label("hour"),
            func.count(Event.id).label("cnt"),
        )
        .where(
            Event.created_at >= cutoff,
            Event.deleted_at.is_(None),
        )
        .group_by("hour")
        .order_by("hour")
    )
    hours: dict[int, int] = {h: 0 for h in range(24)}
    now_hour = datetime.now(UTC).hour
    for row in result.all():
        h = row[0].hour
        bucket = (h - now_hour - 1) % 24
        hours[bucket] = int(row[1])
    return [hours[h] for h in range(24)]


async def recent_events_for_dashboard(
    session: AsyncSession, limit: int = 10
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(
            Event.id,
            Event.name,
            Event.emoji,
            Event.created_at,
            Event.status,
            Location.name.label("location_name"),
        )
        .join(Location, Event.location_id == Location.id)
        .where(Event.deleted_at.is_(None))
        .order_by(Event.created_at.desc())
        .limit(limit)
    )
    return [dict(row._mapping) for row in result]


async def recent_crawl_jobs_for_dashboard(
    session: AsyncSession, limit: int = 3
) -> list[CrawlJob]:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(CrawlJob)
        .order_by(CrawlJob.started_at.desc())
        .limit(limit)
        .options(selectinload(CrawlJob.summary))
    )
    return list(result.scalars().all())


async def sources_by_tier(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(Source.tier, func.count(Source.id))
        .where(Source.deleted_at.is_(None))
        .group_by(Source.tier)
        .order_by(Source.tier)
    )
    return {row[0]: row[1] for row in result.all()}


async def events_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(Event.status, func.count(Event.id))
        .where(Event.deleted_at.is_(None))
        .group_by(Event.status)
    )
    return {row[0]: row[1] for row in result.all()}


# ============================================================================
# Source queries
# ============================================================================


async def list_sources(
    session: AsyncSession,
    *,
    search: str = "",
    tier: int | None = None,
    active_only: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from api.models.event import EventSource
    from api.models.source import CrawlConfig, SourceUrl

    url_subq = (
        select(SourceUrl.url)
        .where(
            SourceUrl.source_id == Source.id,
            SourceUrl.deleted_at.is_(None),
        )
        .order_by(SourceUrl.sort_order)
        .limit(1)
        .correlate(Source)
        .scalar_subquery()
    )

    config_subq = (
        select(CrawlConfig.last_crawled_at)
        .where(CrawlConfig.source_id == Source.id)
        .correlate(Source)
        .scalar_subquery()
    )

    event_count_subq = (
        select(func.count(EventSource.id))
        .where(
            EventSource.source_id == Source.id,
        )
        .correlate(Source)
        .scalar_subquery()
    )

    query = (
        select(
            Source,
            func.coalesce(url_subq, "").label("website"),
            config_subq.label("last_crawled_at"),
            func.coalesce(event_count_subq, 0).label("event_count"),
        )
        .where(Source.deleted_at.is_(None))
    )
    if active_only:
        query = query.where(Source.disabled.is_(False))
    if tier is not None:
        query = query.where(Source.tier == tier)
    if search:
        query = query.where(Source.name.ilike(f"%{search}%"))
    result = await session.execute(
        query.order_by(Source.name).offset(offset).limit(limit)
    )
    rows = result.all()
    return [
        {
            "id": row[0].id,
            "name": row[0].name,
            "type": str(row[0].type),
            "tier": row[0].tier,
            "trust_level": row[0].trust_level,
            "disabled": row[0].disabled,
            "website": str(row[1]) if row[1] else "",
            "last_crawled_at": row[2],
            "event_count": int(row[3]) if row[3] else 0,
        }
        for row in rows
    ]


async def count_sources(
    session: AsyncSession,
    *,
    search: str = "",
    tier: int | None = None,
    active_only: bool = False,
) -> int:
    query = select(func.count(Source.id)).where(Source.deleted_at.is_(None))
    if active_only:
        query = query.where(Source.disabled.is_(False))
    if tier is not None:
        query = query.where(Source.tier == tier)
    if search:
        query = query.where(Source.name.ilike(f"%{search}%"))
    result = await session.scalar(query)
    return result or 0


async def get_source_with_relations(
    session: AsyncSession, source_id: int
) -> Source | None:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Source)
        .where(Source.id == source_id, Source.deleted_at.is_(None))
        .options(
            selectinload(Source.urls),
            selectinload(Source.crawl_config),
        )
    )
    return result.scalar_one_or_none()


# ============================================================================
# Event queries
# ============================================================================


async def list_events(
    session: AsyncSession,
    *,
    search: str = "",
    status: str = "",
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from api.models import EventOccurrence

    occurrence_count_subq = (
        select(
            EventOccurrence.event_id,
            func.count(EventOccurrence.id).label("cnt"),
        )
        .group_by(EventOccurrence.event_id)
        .subquery()
    )

    query = (
        select(
            Event.id,
            Event.name,
            Event.status,
            Event.emoji,
            Event.created_at,
            Location.name.label("location_name"),
            func.coalesce(occurrence_count_subq.c.cnt, 0).label("occurrences"),
        )
        .join(Location, Event.location_id == Location.id)
        .outerjoin(occurrence_count_subq, Event.id == occurrence_count_subq.c.event_id)
        .where(Event.deleted_at.is_(None))
    )
    if status:
        query = query.where(Event.status == status)
    if search:
        query = query.where(Event.name.ilike(f"%{search}%"))
    result = await session.execute(
        query.order_by(Event.created_at.desc()).offset(offset).limit(limit)
    )
    return [dict(row._mapping) for row in result]


async def count_events(
    session: AsyncSession,
    *,
    search: str = "",
    status: str = "",
) -> int:
    query = select(func.count(Event.id)).where(Event.deleted_at.is_(None))
    if status:
        query = query.where(Event.status == status)
    if search:
        query = query.where(Event.name.ilike(f"%{search}%"))
    result = await session.scalar(query)
    return result or 0


async def get_event_with_relations(
    session: AsyncSession, event_id: int
) -> Event | None:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Event)
        .where(Event.id == event_id, Event.deleted_at.is_(None))
        .options(
            selectinload(Event.location),
            selectinload(Event.occurrences),
            selectinload(Event.urls),
            selectinload(Event.tags),
            selectinload(Event.sources),
        )
    )
    return result.scalar_one_or_none()


# ============================================================================
# Location queries
# ============================================================================


async def list_locations(
    session: AsyncSession,
    *,
    search: str = "",
    location_type: str = "",
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    event_count_subq = (
        select(Event.location_id, func.count(Event.id).label("cnt"))
        .where(Event.deleted_at.is_(None))
        .group_by(Event.location_id)
        .subquery()
    )
    query = (
        select(
            Location.id,
            Location.name,
            Location.type,
            Location.lat,
            Location.lng,
            Location.address,
            func.coalesce(event_count_subq.c.cnt, 0).label("event_count"),
        )
        .outerjoin(event_count_subq, Location.id == event_count_subq.c.location_id)
        .where(Location.deleted_at.is_(None))
    )
    if location_type:
        query = query.where(Location.type == location_type)
    if search:
        query = query.where(Location.name.ilike(f"%{search}%"))
    result = await session.execute(
        query.order_by(Location.name).offset(offset).limit(limit)
    )
    return [dict(row._mapping) for row in result]


async def count_locations(
    session: AsyncSession,
    *,
    search: str = "",
    location_type: str = "",
) -> int:
    query = select(func.count(Location.id)).where(Location.deleted_at.is_(None))
    if location_type:
        query = query.where(Location.type == location_type)
    if search:
        query = query.where(Location.name.ilike(f"%{search}%"))
    result = await session.scalar(query)
    return result or 0


async def get_location_with_relations(
    session: AsyncSession, location_id: int
) -> Location | None:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Location)
        .where(Location.id == location_id, Location.deleted_at.is_(None))
        .options(
            selectinload(Location.alternate_names),
            selectinload(Location.events),
            selectinload(Location.tags),
        )
    )
    return result.scalar_one_or_none()


# ============================================================================
# Tag Rule queries
# ============================================================================


async def list_tag_rules(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 50,
) -> list[TagRule]:
    result = await session.execute(
        select(TagRule)
        .order_by(TagRule.rule_type, TagRule.pattern)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_tag_rules(session: AsyncSession) -> int:
    result = await session.scalar(select(func.count(TagRule.id)))
    return result or 0


# ============================================================================
# User queries
# ============================================================================


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.email))
    return list(result.scalars().all())


# ============================================================================
# Log / error queries
# ============================================================================


async def recent_crawl_errors(
    session: AsyncSession, limit: int = 50
) -> list[CrawlResult]:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(CrawlResult)
        .where(
            CrawlResult.status == "failed",
            CrawlResult.error_message.isnot(None),
        )
        .order_by(CrawlResult.created_at.desc())
        .limit(limit)
        .options(selectinload(CrawlResult.source))
    )
    return list(result.scalars().all())


async def recent_crawl_results(
    session: AsyncSession, limit: int = 100
) -> list[CrawlResult]:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(CrawlResult)
        .order_by(CrawlResult.created_at.desc())
        .limit(limit)
        .options(selectinload(CrawlResult.source))
    )
    return list(result.scalars().all())


# ============================================================================
# Processing helpers
# ============================================================================


async def count_extracted_results(session: AsyncSession) -> int:
    result = await session.scalar(
        select(func.count(CrawlResult.id)).where(
            CrawlResult.status == "extracted"
        )
    )
    return result or 0


async def get_jobs_with_extracted_results(session: AsyncSession) -> list[int]:
    result = await session.execute(
        select(CrawlResult.crawl_job_id)
        .where(
            CrawlResult.status == "extracted",
            CrawlResult.crawl_job_id.isnot(None),
        )
        .distinct()
        .order_by(CrawlResult.crawl_job_id)
    )
    return [row[0] for row in result.all()]
