from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from api.models.base import CrawlJobStatus, CrawlResultStatus

__all__ = [
    "CrawlContentResponse",
    "CrawlSummaryResponse",
    "CrawlUrlResultResponse",
    "ExtractedEventResponse",
    "ExtractedEventListItem",
    "CrawlResultResponse",
    "CrawlResultDetailResponse",
    "CrawlJobResponse",
    "CrawlJobDetailResponse",
    "CrawlJobListItem",
]


# ---------------------------------------------------------------------------
# CrawlContent
# ---------------------------------------------------------------------------


class CrawlSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crawl_job_id: int
    api_calls: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    estimated_cost: Decimal
    created_at: datetime


class CrawlContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crawled_content: str | None = None
    extracted_content: str | None = None


# ---------------------------------------------------------------------------
# CrawlUrlResult
# ---------------------------------------------------------------------------


class CrawlUrlResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crawl_result_id: int
    url: str
    status: CrawlResultStatus
    error_message: str | None = None
    crawled_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# ExtractedEvent
# ---------------------------------------------------------------------------


class ExtractedEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crawl_result_id: int
    name: str
    short_name: str | None = None
    description: str | None = None
    emoji: str | None = None
    location_id: int | None = None
    location_name: str | None = None
    sublocation: str | None = None
    url: str | None = None
    occurrences: Any = None
    tags: Any = None
    created_at: datetime


class ExtractedEventListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location_name: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# CrawlResult
# ---------------------------------------------------------------------------


class CrawlResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crawl_job_id: int
    source_id: int
    status: CrawlResultStatus
    crawled_at: datetime | None = None
    extracted_at: datetime | None = None
    processed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


class CrawlResultDetailResponse(CrawlResultResponse):
    extracted_events: list[ExtractedEventListItem] = []
    content: CrawlContentResponse | None = None
    url_results: list[CrawlUrlResultResponse] = []


# ---------------------------------------------------------------------------
# CrawlJob
# ---------------------------------------------------------------------------


class CrawlJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: CrawlJobStatus
    started_at: datetime
    completed_at: datetime | None = None


class CrawlJobDetailResponse(CrawlJobResponse):
    results: list[CrawlResultResponse] = []
    summary: CrawlSummaryResponse | None = None


class CrawlJobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: CrawlJobStatus
    started_at: datetime
    completed_at: datetime | None = None
