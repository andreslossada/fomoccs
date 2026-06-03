"""SQLAdmin ModelAdmin views for all FomoCCS models."""

from sqladmin import ModelView
from sqlalchemy import Select, func, select
from starlette.requests import Request

from api.models.crawl import CrawlContent, CrawlJob, CrawlResult, ExtractedEvent
from api.models.event import Event, EventOccurrence, EventSource
from api.models.location import Location
from api.models.source import CrawlConfig, Source
from api.models.tag import Tag, TagRule
from api.models.user import User

# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class LocationAdmin(ModelView, model=Location):
    name = "Location"
    name_plural = "Locations"
    icon = "fa-solid fa-map-marker-alt"
    column_list = ["id", "name", "address", "lat", "lng", "emoji", "type"]
    column_searchable_list = ["name"]

    def list_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Location).where(Location.active())

    def count_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(func.count(Location.id)).where(Location.active())

    def details_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Location).where(Location.active())


class SourceAdmin(ModelView, model=Source):
    name = "Source"
    name_plural = "Sources"
    icon = "fa-solid fa-globe"
    column_list = [
        "id",
        "name",
        "type",
        "trust_level",
        "disabled",
    ]
    column_searchable_list = ["name"]

    def list_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Source).where(Source.active())

    def count_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(func.count(Source.id)).where(Source.active())

    def details_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Source).where(Source.active())


class EventAdmin(ModelView, model=Event):
    name = "Event"
    name_plural = "Events"
    icon = "fa-solid fa-calendar"
    column_list = [
        "id",
        "name",
        "emoji",
        "location_id",
        "status",
    ]
    column_searchable_list = ["name"]

    def list_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Event).where(Event.active())

    def count_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(func.count(Event.id)).where(Event.active())

    def details_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(Event).where(Event.active())


class TagAdmin(ModelView, model=Tag):
    name = "Tag"
    name_plural = "Tags"
    icon = "fa-solid fa-tag"
    column_list = ["id", "name"]
    column_searchable_list = ["name"]


# ---------------------------------------------------------------------------
# Crawl models
# ---------------------------------------------------------------------------


class CrawlJobAdmin(ModelView, model=CrawlJob):
    name = "Crawl Job"
    name_plural = "Crawl Jobs"
    icon = "fa-solid fa-spider"
    column_list = ["id", "status", "started_at", "completed_at"]


class CrawlResultAdmin(ModelView, model=CrawlResult):
    name = "Crawl Result"
    name_plural = "Crawl Results"
    icon = "fa-solid fa-file-lines"
    column_list = ["id", "source_id", "crawl_job_id", "status"]


class CrawlContentAdmin(ModelView, model=CrawlContent):
    name = "Crawl Content"
    name_plural = "Crawl Contents"
    icon = "fa-solid fa-file-alt"
    column_list = ["id", "crawl_result_id"]
    # Exclude large text fields from forms, detail views, and exports
    form_excluded_columns = ["crawled_content", "extracted_content"]
    column_details_exclude_list = ["crawled_content", "extracted_content"]
    column_export_exclude_list = ["crawled_content", "extracted_content"]


class ExtractedEventAdmin(ModelView, model=ExtractedEvent):
    name = "Extracted Event"
    name_plural = "Extracted Events"
    icon = "fa-solid fa-calendar-check"
    column_list = ["id", "crawl_result_id", "name", "created_at"]
    column_searchable_list = ["name"]


class CrawlConfigAdmin(ModelView, model=CrawlConfig):
    name = "Crawl Config"
    name_plural = "Crawl Configs"
    icon = "fa-solid fa-cog"
    column_list = ["id", "source_id", "crawl_mode", "crawl_frequency"]


# ---------------------------------------------------------------------------
# System models
# ---------------------------------------------------------------------------


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    can_create = False
    column_list = ["id", "email", "display_name", "is_admin", "last_login_at"]
    # Never expose password_hash in forms or detail views
    form_excluded_columns = ["password_hash"]
    column_details_exclude_list = ["password_hash"]


# ---------------------------------------------------------------------------
# Additional / secondary models
# ---------------------------------------------------------------------------


class TagRuleAdmin(ModelView, model=TagRule):
    name = "Tag Rule"
    name_plural = "Tag Rules"
    icon = "fa-solid fa-gavel"
    column_list = ["id", "rule_type", "pattern", "replacement"]

    def list_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(TagRule).where(TagRule.active())

    def count_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(func.count(TagRule.id)).where(TagRule.active())

    def details_query(self, request: Request) -> Select:  # type: ignore[type-arg]
        return select(TagRule).where(TagRule.active())


class EventOccurrenceAdmin(ModelView, model=EventOccurrence):
    name = "Event Occurrence"
    name_plural = "Event Occurrences"
    icon = "fa-solid fa-clock"
    column_list = ["id", "event_id", "start_date", "start_time", "end_date", "end_time"]


class EventSourceAdmin(ModelView, model=EventSource):
    name = "Event Source"
    name_plural = "Event Sources"
    icon = "fa-solid fa-link"
    column_list = [
        "id",
        "event_id",
        "extracted_event_id",
        "source_id",
        "is_primary",
        "created_at",
    ]


# All view classes in registration order
ALL_VIEWS: list[type[ModelView]] = [
    LocationAdmin,
    SourceAdmin,
    EventAdmin,
    TagAdmin,
    CrawlJobAdmin,
    CrawlResultAdmin,
    CrawlContentAdmin,
    ExtractedEventAdmin,
    CrawlConfigAdmin,
    UserAdmin,
    TagRuleAdmin,
    EventOccurrenceAdmin,
    EventSourceAdmin,
]
