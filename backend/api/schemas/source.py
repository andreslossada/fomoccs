from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from api.models.base import CrawlMode, SourceType

__all__ = [
    "SourceUrlResponse",
    "SourceUrlCreate",
    "CrawlConfigResponse",
    "CrawlConfigCreate",
    "CrawlConfigUpdate",
    "SourceResponse",
    "SourceDetailResponse",
    "SourceCreate",
    "SourceUpdate",
    "SourceListItem",
]


# ---------------------------------------------------------------------------
# SourceUrl
# ---------------------------------------------------------------------------


class SourceUrlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    js_code: str | None = None
    sort_order: int = 0
    deleted_at: datetime | None = None


class SourceUrlCreate(BaseModel):
    url: Annotated[AnyHttpUrl, Field(max_length=2000)]
    js_code: str | None = None
    sort_order: int = 0


# ---------------------------------------------------------------------------
# CrawlConfig
# ---------------------------------------------------------------------------


class CrawlConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    notes: str | None = None
    default_tags: list[str] | None = None
    crawl_frequency: int
    crawl_frequency_locked: bool = False
    crawl_after: date | None = None
    force_crawl: bool = False
    last_crawled_at: datetime | None = None
    crawl_mode: CrawlMode
    selector: str | None = None
    num_clicks: int | None = None
    js_code: str | None = None
    keywords: str | None = None
    max_pages: int = 30
    max_batches: int | None = None
    json_api_config: dict[str, Any] | None = None
    delay_before_return_html: int | None = None
    content_filter_threshold: float | None = None
    scan_full_page: bool | None = None
    remove_overlay_elements: bool | None = None
    javascript_enabled: bool | None = None
    text_mode: bool | None = None
    light_mode: bool | None = None
    use_stealth: bool | None = None
    scroll_delay: float | None = None
    crawl_timeout: int | None = None
    process_images: bool | None = None


class CrawlConfigCreate(BaseModel):
    crawl_frequency: int
    crawl_mode: CrawlMode
    notes: str | None = None
    default_tags: list[str] | None = None
    crawl_frequency_locked: bool = False
    crawl_after: date | None = None
    force_crawl: bool = False
    selector: Annotated[str | None, Field(max_length=500)] = None
    num_clicks: int | None = None
    js_code: str | None = None
    keywords: Annotated[str | None, Field(max_length=255)] = None
    max_pages: int = 30
    max_batches: int | None = None
    json_api_config: dict[str, Any] | None = None
    delay_before_return_html: int | None = None
    content_filter_threshold: float | None = None
    scan_full_page: bool | None = None
    remove_overlay_elements: bool | None = None
    javascript_enabled: bool | None = None
    text_mode: bool | None = None
    light_mode: bool | None = None
    use_stealth: bool | None = None
    scroll_delay: float | None = None
    crawl_timeout: int | None = None
    process_images: bool | None = None


class CrawlConfigUpdate(BaseModel):
    crawl_frequency: int | None = None
    crawl_mode: CrawlMode | None = None
    notes: str | None = None
    default_tags: list[str] | None = None
    crawl_frequency_locked: bool | None = None
    crawl_after: date | None = None
    force_crawl: bool | None = None
    selector: Annotated[str | None, Field(max_length=500)] = None
    num_clicks: int | None = None
    js_code: str | None = None
    keywords: Annotated[str | None, Field(max_length=255)] = None
    max_pages: int | None = None
    max_batches: int | None = None
    json_api_config: dict[str, Any] | None = None
    delay_before_return_html: int | None = None
    content_filter_threshold: float | None = None
    scan_full_page: bool | None = None
    remove_overlay_elements: bool | None = None
    javascript_enabled: bool | None = None
    text_mode: bool | None = None
    light_mode: bool | None = None
    use_stealth: bool | None = None
    scroll_delay: float | None = None
    crawl_timeout: int | None = None
    process_images: bool | None = None


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: SourceType
    trust_level: float | None = None
    disabled: bool = False
    tier: int = 1
    min_request_interval_seconds: float | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class SourceDetailResponse(SourceResponse):
    urls: list[SourceUrlResponse] = []
    crawl_config: CrawlConfigResponse | None = None


class SourceCreate(BaseModel):
    name: Annotated[str, Field(max_length=255)]
    type: SourceType
    trust_level: Decimal | None = None
    disabled: bool = False
    tier: int = 1
    min_request_interval_seconds: float | None = None
    urls: list[SourceUrlCreate] = []
    crawl_config: CrawlConfigCreate | None = None


class SourceUpdate(BaseModel):
    name: Annotated[str | None, Field(max_length=255)] = None
    type: SourceType | None = None
    trust_level: Decimal | None = None
    disabled: bool | None = None
    tier: int | None = None
    min_request_interval_seconds: float | None = None


class SourceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: SourceType
    trust_level: float | None = None
    disabled: bool = False
    tier: int = 1
    min_request_interval_seconds: float | None = None
    deleted_at: datetime | None = None
