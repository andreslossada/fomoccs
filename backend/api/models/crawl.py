from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base
from api.models.base import CrawlJobStatus, CrawlResultStatus, ExtractedEventStatus

# Shared enum type instances — reused across models to avoid duplicate
# CREATE TYPE statements during metadata.create_all().
_crawl_result_status_enum = Enum(CrawlResultStatus, name="crawl_result_status")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    status: Mapped[CrawlJobStatus] = mapped_column(
        Enum(CrawlJobStatus, name="crawl_job_status"),
        server_default="running",
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    # Relationships
    results: Mapped[list["CrawlResult"]] = relationship(back_populates="crawl_job")
    summary: Mapped["CrawlSummary | None"] = relationship(
        back_populates="crawl_job", uselist=False
    )


class CrawlResult(Base):
    __tablename__ = "crawl_results"
    __table_args__ = (UniqueConstraint("crawl_job_id", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    crawl_job_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_jobs.id", ondelete="CASCADE")
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    status: Mapped[CrawlResultStatus] = mapped_column(
        _crawl_result_status_enum,
        server_default="pending",
    )
    crawled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    extracted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )

    # LLM provider tracking (multi-model fallback chain)
    extraction_provider: Mapped[str | None] = mapped_column(String(100))
    extraction_model: Mapped[str | None] = mapped_column(String(200))
    extraction_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    extraction_fallbacks: Mapped[int] = mapped_column(Integer, server_default="0")

    # Relationships
    crawl_job: Mapped["CrawlJob"] = relationship(back_populates="results")
    source: Mapped["Source"] = relationship(back_populates="crawl_results")
    extracted_events: Mapped[list["ExtractedEvent"]] = relationship(
        back_populates="crawl_result"
    )
    content: Mapped["CrawlContent | None"] = relationship(
        back_populates="crawl_result", uselist=False
    )
    url_results: Mapped[list["CrawlUrlResult"]] = relationship(
        back_populates="crawl_result"
    )


class CrawlContent(Base):
    __tablename__ = "crawl_contents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    crawl_result_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_results.id", ondelete="CASCADE"), unique=True
    )
    crawled_content: Mapped[str | None] = mapped_column(Text)
    extracted_content: Mapped[str | None] = mapped_column(Text)

    # Relationships
    crawl_result: Mapped["CrawlResult"] = relationship(back_populates="content")


class CrawlUrlResult(Base):
    __tablename__ = "crawl_url_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    crawl_result_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_results.id", ondelete="CASCADE")
    )
    url: Mapped[str] = mapped_column(String(2000))
    status: Mapped[CrawlResultStatus] = mapped_column(
        _crawl_result_status_enum,
        server_default="pending",
    )
    crawled_content: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    crawled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )

    # Relationships
    crawl_result: Mapped["CrawlResult"] = relationship(back_populates="url_results")


class ExtractedEvent(Base):
    __tablename__ = "extracted_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    crawl_result_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_results.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(500))
    short_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    emoji: Mapped[str | None] = mapped_column(String(10))
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL")
    )
    location_name: Mapped[str | None] = mapped_column(String(255))
    sublocation: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2000))
    occurrences: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )

    # Relationships
    crawl_result: Mapped["CrawlResult"] = relationship(
        back_populates="extracted_events"
    )
    location: Mapped["Location"] = relationship()
    event_sources: Mapped[list["EventSource"]] = relationship(
        back_populates="extracted_event"
    )
    logs: Mapped[list["ExtractedEventLog"]] = relationship(
        back_populates="extracted_event"
    )


class ExtractedEventLog(Base):
    __tablename__ = "extracted_event_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    extracted_event_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_events.id", ondelete="CASCADE")
    )
    status: Mapped[ExtractedEventStatus] = mapped_column(
        Enum(ExtractedEventStatus, name="extracted_event_status")
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL")
    )
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )

    # Relationships
    extracted_event: Mapped["ExtractedEvent"] = relationship(back_populates="logs")
    event: Mapped["Event | None"] = relationship()


class CrawlSummary(Base):
    __tablename__ = "crawl_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    crawl_job_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_jobs.id", ondelete="CASCADE"), unique=True
    )
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.current_timestamp()
    )
    # Multi-model fallback tracking
    providers_used: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rate_limited_count: Mapped[int] = mapped_column(Integer, server_default="0")

    # Relationships
    crawl_job: Mapped["CrawlJob"] = relationship(back_populates="summary")
