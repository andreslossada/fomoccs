"""Celery task for async geocoding of locations."""

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.celery_app import celery
from api.config import get_settings
from api.models.location import Location
from api.services.geocoding import geocode_with_fallback
from api.task_names import GEOCODE_LOCATION

logger = logging.getLogger(__name__)


def _make_session() -> async_sessionmaker[AsyncSession]:
    """Create a fresh engine + session factory bound to the current event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _geocode_location(location_id: int) -> None:
    """Fetch location, geocode it, and persist coordinates."""
    settings = get_settings()
    google_api_key = settings.google_maps_api_key
    geoapify_api_key = settings.geoapify_api_key
    if not google_api_key and not geoapify_api_key:
        logger.warning("No geocoding provider keys configured, skipping geocoding")
        return

    session_factory = _make_session()
    async with session_factory() as session:
        location = await session.scalar(
            select(Location).where(Location.id == location_id)
        )
        if location is None:
            logger.warning("Location %d not found, skipping geocoding", location_id)
            return

        if location.lat is not None and location.lng is not None:
            logger.info("Location %d already has coordinates, skipping", location_id)
            return

        result = await geocode_with_fallback(
            location.name,
            address=location.address,
            google_api_key=google_api_key,
            geoapify_api_key=geoapify_api_key,
        )

        if result is None:
            logger.info(
                "No geocoding result for location %d (%s)", location_id, location.name
            )
            return

        location.lat = result.lat
        location.lng = result.lng
        await session.commit()
        logger.info(
            "Geocoded location %d (%s) -> (%f, %f)",
            location_id,
            location.name,
            result.lat,
            result.lng,
        )


@celery.task(bind=True, name=GEOCODE_LOCATION)
def geocode_location(self: Any, location_id: int) -> None:
    """Geocode a location by ID. Retries on API errors with exponential backoff."""
    try:
        asyncio.run(_geocode_location(location_id))
    except httpx.HTTPError as exc:
        countdown = 2**self.request.retries * 30
        logger.warning(
            "Geocoding API error for location %d, retrying in %ds: %s",
            location_id,
            countdown,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)
