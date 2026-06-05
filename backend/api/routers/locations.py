import asyncio
import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from api.celery_app import celery
from api.dependencies import (
    AdminUserDep,
    CurrentUserDep,
    GeocodingKeyDep,
    SessionDep,
)
from api.models.event import Event
from api.models.location import Location, LocationAlternateName, LocationTag
from api.models.tag import Tag
from api.schemas.common import PaginatedResponse
from api.schemas.location import (
    BackfillResponse,
    BulkCreateRequest,
    BulkCreateResponse,
    BulkCreateResultItem,
    GeocodeResponse,
    LocationCreate,
    LocationDetailResponse,
    LocationListItem,
    LocationUpdate,
)
from api.services.geocoding import (
    GeocodingResult,
    geocode_with_fallback,
    normalize_location_name,
)
from api.services.tags import get_or_create_tag
from api.task_names import GEOCODE_LOCATION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/locations", tags=["locations"])

# Module-level semaphore shared across all requests for global rate limiting
_geocode_semaphore = asyncio.Semaphore(5)


async def _refresh_location(db: SessionDep, location_id: int) -> Location:
    """Re-fetch a location with populate_existing to bypass stale viewonly caches."""
    stmt = (
        select(Location)
        .where(Location.id == location_id)
        .options(
            selectinload(Location.alternate_names),
            selectinload(Location.tags),
        )
        .execution_options(populate_existing=True)
    )
    location = await db.scalar(stmt)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return location


async def _get_location_or_404(db: SessionDep, location_id: int) -> Location:
    stmt = (
        select(Location)
        .where(Location.id == location_id, Location.active())
        .options(
            selectinload(Location.alternate_names),
            selectinload(Location.tags),
        )
    )
    location = await db.scalar(stmt)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return location


@router.get("/", response_model=PaginatedResponse[LocationListItem])
async def list_locations(
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_deleted: bool = False,
) -> PaginatedResponse[LocationListItem]:
    event_count_sq = (
        select(func.count(Event.id))
        .where(Event.location_id == Location.id, Event.active())
        .correlate(Location)
        .scalar_subquery()
        .label("event_count")
    )

    stmt = (
        select(Location, event_count_sq)
        .order_by(Location.name)
        .limit(limit)
        .offset(offset)
    )
    total_stmt = select(func.count(Location.id))

    if not include_deleted:
        stmt = stmt.where(Location.active())
        total_stmt = total_stmt.where(Location.active())

    result = await db.execute(stmt)
    rows = result.all()
    total = await db.scalar(total_stmt) or 0

    data = [
        LocationListItem(
            id=loc.id,
            name=loc.name,
            short_name=loc.short_name,
            very_short_name=loc.very_short_name,
            emoji=loc.emoji,
            event_count=event_count,
        )
        for loc, event_count in rows
    ]
    return PaginatedResponse(data=data, total=total)


@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_locations(
    data: BulkCreateRequest,
    db: SessionDep,
    user: CurrentUserDep,
    keys: GeocodingKeyDep,
) -> BulkCreateResponse:
    """Create multiple locations with automatic geocoding and dedup."""
    # -- Build dedup set from existing locations + alternate names --
    name_stmt = select(Location.name).where(Location.active())
    alt_stmt = (
        select(LocationAlternateName.alternate_name)
        .join(Location)
        .where(Location.active())
    )
    name_rows = await db.execute(name_stmt)
    alt_rows = await db.execute(alt_stmt)

    existing_names: set[str] = set()
    for (name,) in name_rows:
        normalized = normalize_location_name(name)
        if normalized:
            existing_names.add(normalized)
    for (alt_name,) in alt_rows:
        normalized = normalize_location_name(alt_name)
        if normalized:
            existing_names.add(normalized)

    async def _geocode(
        idx: int, loc: LocationCreate, client: httpx.AsyncClient
    ) -> tuple[int, GeocodingResult | None]:
        if loc.lat is not None and loc.lng is not None:
            return idx, None  # already has coords
        async with _geocode_semaphore:
            return idx, await geocode_with_fallback(
                loc.name,
                address=loc.address,
                google_api_key=keys.google_api_key,
                geoapify_api_key=keys.geoapify_api_key,
                client=client,
            )

    # Filter out duplicates before geocoding (save API calls)
    non_duplicate_indices: list[int] = []
    results: list[BulkCreateResultItem] = []
    created_count = 0
    error_count = 0

    for i, loc_data in enumerate(data.locations):
        normalized = normalize_location_name(loc_data.name)
        if normalized and normalized in existing_names:
            results.append(
                BulkCreateResultItem(
                    index=i,
                    status="duplicate",
                    error=f"Location '{loc_data.name}' already exists",
                )
            )
            continue
        # Also add to set to catch duplicates within the batch
        if normalized:
            existing_names.add(normalized)
        non_duplicate_indices.append(i)

    # Pre-fetch all unique tags in two queries instead of O(n) per-item lookups
    all_tag_names: set[str] = set()
    for i in non_duplicate_indices:
        all_tag_names.update(data.locations[i].tags)

    tag_map: dict[str, Tag] = {}
    if all_tag_names:
        existing_tags_stmt = select(Tag).where(Tag.name.in_(all_tag_names))
        existing_tags_result = await db.execute(existing_tags_stmt)
        for tag in existing_tags_result.scalars():
            tag_map[tag.name] = tag

    # Geocode only non-duplicate items in parallel, sharing one httpx client
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        geocode_tasks = [
            _geocode(i, data.locations[i], http_client) for i in non_duplicate_indices
        ]
        geocode_results_raw = await asyncio.gather(
            *geocode_tasks, return_exceptions=True
        )

    # Build index -> result map; log any exceptions
    geocode_map: dict[int, GeocodingResult | None] = {}
    for item in geocode_results_raw:
        if isinstance(item, BaseException):
            logger.warning("Geocode task failed: %s", item)
            continue
        idx, result = item
        geocode_map[idx] = result

    for i in non_duplicate_indices:
        loc_data = data.locations[i]
        savepoint = await db.begin_nested()
        try:
            geo_result = geocode_map.get(i)
            lat = loc_data.lat
            lng = loc_data.lng

            if geo_result is not None:
                lat = geo_result.lat
                lng = geo_result.lng

            location = Location(
                name=loc_data.name,
                short_name=loc_data.short_name,
                very_short_name=loc_data.very_short_name,
                address=loc_data.address,
                description=loc_data.description,
                lat=lat,
                lng=lng,
                emoji=loc_data.emoji,
                alt_emoji=loc_data.alt_emoji,
                website_url=loc_data.website_url,
                type=loc_data.type,
            )
            db.add(location)
            await db.flush()

            for alt_name in loc_data.alternate_names:
                db.add(
                    LocationAlternateName(
                        location_id=location.id, alternate_name=alt_name
                    )
                )
            for tag_name in loc_data.tags:
                if tag_name in tag_map:
                    tag = tag_map[tag_name]
                else:
                    tag = await get_or_create_tag(db, tag_name)
                    tag_map[tag_name] = tag
                db.add(LocationTag(location_id=location.id, tag_id=tag.id))

            await savepoint.commit()

            refreshed = await _refresh_location(db, location.id)
            item_status: Literal["created", "geocode_failed"] = "created"
            if loc_data.lat is None and loc_data.lng is None and lat is None:
                item_status = "geocode_failed"

            results.append(
                BulkCreateResultItem(
                    index=i,
                    status=item_status,
                    location=LocationDetailResponse.model_validate(refreshed),
                )
            )
            created_count += 1
        except IntegrityError:
            await savepoint.rollback()
            results.append(
                BulkCreateResultItem(
                    index=i,
                    status="duplicate",
                    error=f"Location '{loc_data.name}' already exists (race)",
                )
            )
        except Exception as exc:
            await savepoint.rollback()
            error_count += 1
            results.append(
                BulkCreateResultItem(index=i, status="error", error=str(exc))
            )

    await db.commit()
    results.sort(key=lambda r: r.index)
    return BulkCreateResponse(
        total=len(data.locations),
        created=created_count,
        errors=error_count,
        results=results,
    )


@router.post("/backfill-geocode", response_model=BackfillResponse)
async def backfill_geocode(
    db: SessionDep,
    user: AdminUserDep,
    keys: GeocodingKeyDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> BackfillResponse:
    """Geocode active locations missing coordinates. Admin only."""
    stmt = (
        select(Location)
        .where(
            Location.active(),
            or_(Location.lat.is_(None), Location.lng.is_(None)),
        )
        .options(
            selectinload(Location.alternate_names),
            selectinload(Location.tags),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    locations = list(result.scalars().all())

    if not locations:
        return BackfillResponse(total_processed=0, geocoded=0, failed=0, skipped=0)

    async def _geocode(
        loc: Location, client: httpx.AsyncClient
    ) -> tuple[int, GeocodingResult | None]:
        async with _geocode_semaphore:
            return loc.id, await geocode_with_fallback(
                loc.name,
                address=loc.address,
                google_api_key=keys.google_api_key,
                geoapify_api_key=keys.geoapify_api_key,
                client=client,
            )

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        geocode_tasks = [_geocode(loc, http_client) for loc in locations]
        geocode_results_raw = await asyncio.gather(
            *geocode_tasks, return_exceptions=True
        )

    geocode_map: dict[int, GeocodingResult | None] = {}
    for item in geocode_results_raw:
        if isinstance(item, BaseException):
            logger.warning("Backfill geocode task failed: %s", item)
            continue
        loc_id, geo_result = item
        geocode_map[loc_id] = geo_result

    geocoded = 0
    failed = 0

    for loc in locations:
        geo_result = geocode_map.get(loc.id)
        if geo_result is not None:
            loc.lat = geo_result.lat
            loc.lng = geo_result.lng
            geocoded += 1
        else:
            failed += 1

    await db.commit()
    return BackfillResponse(
        total_processed=len(locations),
        geocoded=geocoded,
        failed=failed,
        skipped=0,
    )


@router.get("/{location_id}", response_model=LocationDetailResponse)
async def get_location(
    location_id: int,
    db: SessionDep,
) -> LocationDetailResponse:
    location = await _get_location_or_404(db, location_id)
    return LocationDetailResponse.model_validate(location)


@router.post(
    "/", response_model=LocationDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_location(
    data: LocationCreate,
    db: SessionDep,
    user: CurrentUserDep,
) -> LocationDetailResponse:
    location = Location(
        name=data.name,
        short_name=data.short_name,
        very_short_name=data.very_short_name,
        address=data.address,
        description=data.description,
        lat=data.lat,
        lng=data.lng,
        emoji=data.emoji,
        alt_emoji=data.alt_emoji,
        website_url=data.website_url,
        type=data.type,
    )
    db.add(location)
    await db.flush()

    for alt_name in data.alternate_names:
        db.add(LocationAlternateName(location_id=location.id, alternate_name=alt_name))

    for tag_name in data.tags:
        tag = await get_or_create_tag(db, tag_name)
        db.add(LocationTag(location_id=location.id, tag_id=tag.id))

    location_id = location.id  # capture before commit expires attributes

    await db.commit()

    if data.lat is None and data.lng is None:
        celery.send_task(GEOCODE_LOCATION, args=[location_id])

    location = await _refresh_location(db, location_id)
    return LocationDetailResponse.model_validate(location)


@router.post("/{location_id}/geocode", response_model=GeocodeResponse)
async def geocode_location(
    location_id: int,
    db: SessionDep,
    user: CurrentUserDep,
    keys: GeocodingKeyDep,
    force: bool = False,
) -> GeocodeResponse:
    """Geocode (or re-geocode) a single location."""
    location = await _get_location_or_404(db, location_id)

    if location.lat is not None and location.lng is not None and not force:
        return GeocodeResponse(
            lat=location.lat,
            lng=location.lng,
            formatted_address=location.address,
            confidence=None,
            geocoded=True,
        )

    result = await geocode_with_fallback(
        location.name,
        address=location.address,
        google_api_key=keys.google_api_key,
        geoapify_api_key=keys.geoapify_api_key,
    )

    if result is None:
        return GeocodeResponse(geocoded=False)

    location.lat = result.lat
    location.lng = result.lng
    if not location.address and result.formatted_address:
        location.address = result.formatted_address
    await db.commit()

    return GeocodeResponse(
        lat=result.lat,
        lng=result.lng,
        formatted_address=result.formatted_address,
        confidence=result.confidence,
        geocoded=True,
    )


@router.put("/{location_id}", response_model=LocationDetailResponse)
async def update_location(
    location_id: int,
    data: LocationUpdate,
    db: SessionDep,
    user: CurrentUserDep,
) -> LocationDetailResponse:
    location = await _get_location_or_404(db, location_id)

    # Apply scalar updates
    update_fields = data.model_dump(exclude_unset=True)
    scalar_fields = {
        k: v for k, v in update_fields.items() if k not in ("alternate_names", "tags")
    }
    for field, value in scalar_fields.items():
        setattr(location, field, value)

    # Replace alternate_names if provided
    if "alternate_names" in update_fields:
        await db.execute(
            delete(LocationAlternateName).where(
                LocationAlternateName.location_id == location.id
            )
        )
        for alt_name in data.alternate_names:  # type: ignore[union-attr]
            db.add(
                LocationAlternateName(location_id=location.id, alternate_name=alt_name)
            )

    # Replace tags if provided
    if "tags" in update_fields:
        await db.execute(
            delete(LocationTag).where(LocationTag.location_id == location.id)
        )
        for tag_name in data.tags:  # type: ignore[union-attr]
            tag = await get_or_create_tag(db, tag_name)
            db.add(LocationTag(location_id=location.id, tag_id=tag.id))

    await db.commit()
    location = await _refresh_location(db, location_id)
    return LocationDetailResponse.model_validate(location)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> Response:
    location = await _get_location_or_404(db, location_id)

    location.soft_delete()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
