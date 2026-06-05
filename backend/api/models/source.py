from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base
from api.models.base import CrawlMode, SoftDeleteMixin, SourceType, TimestampMixin


class Source(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"),
    )
    trust_level: Mapped[float | None] = mapped_column(Numeric(2, 1, asdecimal=False))
    disabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tier: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    min_request_interval_seconds: Mapped[float | None] = mapped_column(
        Numeric(4, 2, asdecimal=False)
    )

    # Relationships
    urls: Mapped[list["SourceUrl"]] = relationship(back_populates="source")
    crawl_config: Mapped["CrawlConfig | None"] = relationship(
        back_populates="source", uselist=False
    )
    crawl_results: Mapped[list["CrawlResult"]] = relationship(back_populates="source")


class SourceUrl(SoftDeleteMixin, Base):
    __tablename__ = "source_urls"
    __table_args__ = (
        Index(
            "uq_source_urls_url_active",
            "url",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2000))
    js_code: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="urls")


class CrawlConfig(Base):
    __tablename__ = "crawl_configs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    default_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    crawl_frequency: Mapped[int] = mapped_column(Integer)
    crawl_frequency_locked: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    crawl_after: Mapped[date | None] = mapped_column(Date)
    force_crawl: Mapped[bool] = mapped_column(Boolean, server_default="false")
    last_crawled_at: Mapped[datetime | None] = mapped_column()
    crawl_mode: Mapped[CrawlMode] = mapped_column(
        Enum(CrawlMode, name="crawl_mode"),
        server_default="browser",
    )
    selector: Mapped[str | None] = mapped_column(String(500))
    num_clicks: Mapped[int | None] = mapped_column(Integer)
    js_code: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(String(255))
    max_pages: Mapped[int] = mapped_column(Integer, server_default="30")
    max_batches: Mapped[int | None] = mapped_column(Integer)
    json_api_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delay_before_return_html: Mapped[int | None] = mapped_column(Integer)
    content_filter_threshold: Mapped[float | None] = mapped_column(
        Numeric(3, 2, asdecimal=False)
    )
    scan_full_page: Mapped[bool | None] = mapped_column(Boolean)
    remove_overlay_elements: Mapped[bool | None] = mapped_column(Boolean)
    javascript_enabled: Mapped[bool | None] = mapped_column(Boolean)
    text_mode: Mapped[bool | None] = mapped_column(Boolean)
    light_mode: Mapped[bool | None] = mapped_column(Boolean)
    use_stealth: Mapped[bool | None] = mapped_column(Boolean)
    scroll_delay: Mapped[float | None] = mapped_column(Numeric(3, 2, asdecimal=False))
    crawl_timeout: Mapped[int | None] = mapped_column(Integer)
    process_images: Mapped[bool | None] = mapped_column(Boolean)

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="crawl_config")
