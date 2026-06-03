"""Tests for the geocoding Celery task."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.location import Location
from api.services.geocoding import GeocodingResult
from api.tasks.geocoding import _geocode_location


@pytest.fixture
def sample_location_no_coords(db_session: AsyncSession) -> Location:
    loc = Location(name="Test Venue", address="Av. Urdaneta 1234", lat=None, lng=None)
    db_session.add(loc)
    return loc


@pytest.fixture
def sample_location_with_coords(db_session: AsyncSession) -> Location:
    loc = Location(
        name="Known Venue", address="Calle Real 123", lat=10.48, lng=-66.90
    )
    db_session.add(loc)
    return loc


GEO_RESULT = GeocodingResult(
    lat=10.5069,
    lng=-66.9147,
    formatted_address="Plaza Bolívar, Caracas",
    confidence=0.9,
)


class TestGeocodeLocationTask:
    @pytest.mark.asyncio
    async def test_geocodes_and_updates_coords(
        self, db_session: AsyncSession, sample_location_no_coords: Location
    ) -> None:
        await db_session.flush()
        loc_id = sample_location_no_coords.id

        with (
            patch(
                "api.tasks.geocoding._make_session",
                return_value=SessionFactoryStub(db_session),
            ),
            patch(
                "api.tasks.geocoding.get_settings",
                return_value=FakeSettings(geoapify_api_key="fake-key"),
            ),
            patch(
                "api.tasks.geocoding.geocode_location_name",
                new_callable=AsyncMock,
                return_value=GEO_RESULT,
            ) as mock_geocode,
        ):
            await _geocode_location(loc_id)

        mock_geocode.assert_called_once_with(
            "Test Venue", "fake-key", address="Av. Urdaneta 1234"
        )

        loc = await db_session.get(Location, loc_id)
        assert loc is not None
        assert loc.lat == pytest.approx(10.5069)
        assert loc.lng == pytest.approx(-66.9147)

    @pytest.mark.asyncio
    async def test_skips_when_coords_already_set(
        self, db_session: AsyncSession, sample_location_with_coords: Location
    ) -> None:
        await db_session.flush()
        loc_id = sample_location_with_coords.id

        with (
            patch(
                "api.tasks.geocoding._make_session",
                return_value=SessionFactoryStub(db_session),
            ),
            patch(
                "api.tasks.geocoding.get_settings",
                return_value=FakeSettings(geoapify_api_key="fake-key"),
            ),
            patch(
                "api.tasks.geocoding.geocode_location_name",
                new_callable=AsyncMock,
            ) as mock_geocode,
        ):
            await _geocode_location(loc_id)

        mock_geocode.assert_not_called()

    @pytest.mark.asyncio
    async def test_completes_without_retry_when_no_results(
        self, db_session: AsyncSession, sample_location_no_coords: Location
    ) -> None:
        await db_session.flush()
        loc_id = sample_location_no_coords.id

        with (
            patch(
                "api.tasks.geocoding._make_session",
                return_value=SessionFactoryStub(db_session),
            ),
            patch(
                "api.tasks.geocoding.get_settings",
                return_value=FakeSettings(geoapify_api_key="fake-key"),
            ),
            patch(
                "api.tasks.geocoding.geocode_location_name",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await _geocode_location(loc_id)

        loc = await db_session.get(Location, loc_id)
        assert loc is not None
        assert loc.lat is None
        assert loc.lng is None

    @pytest.mark.asyncio
    async def test_completes_when_location_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with (
            patch(
                "api.tasks.geocoding._make_session",
                return_value=SessionFactoryStub(db_session),
            ),
            patch(
                "api.tasks.geocoding.get_settings",
                return_value=FakeSettings(geoapify_api_key="fake-key"),
            ),
        ):
            # Should not raise
            await _geocode_location(99999)

    @pytest.mark.asyncio
    async def test_skips_when_no_api_key(
        self, db_session: AsyncSession, sample_location_no_coords: Location
    ) -> None:
        await db_session.flush()
        loc_id = sample_location_no_coords.id

        with (
            patch(
                "api.tasks.geocoding.get_settings",
                return_value=FakeSettings(geoapify_api_key=""),
            ),
            patch(
                "api.tasks.geocoding.geocode_location_name",
                new_callable=AsyncMock,
            ) as mock_geocode,
        ):
            await _geocode_location(loc_id)

        mock_geocode.assert_not_called()


class TestGeocodeLocationCeleryTask:
    def test_task_is_registered(self):
        from api.celery_app import celery
        from api.task_names import GEOCODE_LOCATION

        assert GEOCODE_LOCATION in celery.tasks

    def test_task_retries_on_http_error(self) -> None:
        from api.tasks.geocoding import geocode_location

        with (
            patch(
                "api.tasks.geocoding._geocode_location",
                side_effect=httpx.HTTPError("Connection failed"),
            ),
            patch.object(
                geocode_location, "retry", side_effect=Exception("retried")
            ) as mock_retry,
        ):
            with pytest.raises(Exception, match="retried"):
                geocode_location(1)

            mock_retry.assert_called_once()
            call_kwargs = mock_retry.call_args[1]
            assert call_kwargs["max_retries"] == 3
            assert isinstance(call_kwargs["exc"], httpx.HTTPError)


# -- Test helpers --


class FakeSettings:
    def __init__(self, geoapify_api_key: str = ""):
        self.geoapify_api_key = geoapify_api_key


class _SessionStub:
    """Wraps a real AsyncSession as an async context manager without closing it."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        pass


class SessionFactoryStub:
    """Mimics async_sessionmaker — calling it returns an _SessionStub."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def __call__(self) -> _SessionStub:
        return _SessionStub(self._session)
