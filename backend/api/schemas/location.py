from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from api.models.base import LocationType
from api.schemas.common import TagResponse

__all__ = [
    "LocationCreate",
    "LocationUpdate",
    "LocationResponse",
    "LocationListItem",
    "AlternateNameResponse",
    "LocationDetailResponse",
    "GeocodeResponse",
    "BulkCreateRequest",
    "BulkCreateResultItem",
    "BulkCreateResponse",
    "BackfillResponse",
]


class LocationCreate(BaseModel):
    name: Annotated[str, Field(max_length=255)]
    short_name: Annotated[str | None, Field(max_length=100)] = None
    very_short_name: Annotated[str | None, Field(max_length=50)] = None
    address: Annotated[str | None, Field(max_length=500)] = None
    description: str | None = None
    lat: Annotated[float | None, Field(ge=-90, le=90)] = None
    lng: Annotated[float | None, Field(ge=-180, le=180)] = None
    emoji: Annotated[str | None, Field(max_length=10)] = None
    alt_emoji: Annotated[str | None, Field(max_length=10)] = None
    website_url: Annotated[AnyHttpUrl | None, Field(max_length=500)] = None
    type: LocationType = LocationType.venue
    alternate_names: list[str] = []
    tags: list[str] = []


class LocationUpdate(BaseModel):
    name: Annotated[str | None, Field(max_length=255)] = None
    short_name: Annotated[str | None, Field(max_length=100)] = None
    very_short_name: Annotated[str | None, Field(max_length=50)] = None
    address: Annotated[str | None, Field(max_length=500)] = None
    description: str | None = None
    lat: Annotated[float | None, Field(ge=-90, le=90)] = None
    lng: Annotated[float | None, Field(ge=-180, le=180)] = None
    emoji: Annotated[str | None, Field(max_length=10)] = None
    alt_emoji: Annotated[str | None, Field(max_length=10)] = None
    website_url: Annotated[AnyHttpUrl | None, Field(max_length=500)] = None
    type: LocationType | None = None
    alternate_names: list[str] | None = None
    tags: list[str] | None = None


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str | None = None
    very_short_name: str | None = None
    address: str | None = None
    description: str | None = None
    lat: float | None = None
    lng: float | None = None
    emoji: str | None = None
    alt_emoji: str | None = None
    website_url: str | None = None
    type: LocationType = LocationType.venue
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class AlternateNameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alternate_name: str


class LocationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str | None = None
    very_short_name: str | None = None
    emoji: str | None = None
    event_count: int = 0
    deleted_at: datetime | None = None


class LocationDetailResponse(LocationResponse):
    alternate_names: list[AlternateNameResponse] = []
    tags: list[TagResponse] = []


# ---------------------------------------------------------------------------
# Geocoding schemas
# ---------------------------------------------------------------------------


class GeocodeResponse(BaseModel):
    lat: float | None = None
    lng: float | None = None
    formatted_address: str | None = None
    confidence: float | None = None
    geocoded: bool


class BulkCreateRequest(BaseModel):
    locations: Annotated[list[LocationCreate], Field(max_length=50)]


class BulkCreateResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    status: Literal["created", "geocode_failed", "error", "duplicate"]
    location: LocationDetailResponse | None = None
    error: str | None = None


class BulkCreateResponse(BaseModel):
    total: int
    created: int
    errors: int
    results: list[BulkCreateResultItem]


class BackfillResponse(BaseModel):
    total_processed: int
    geocoded: int
    failed: int
    skipped: int
