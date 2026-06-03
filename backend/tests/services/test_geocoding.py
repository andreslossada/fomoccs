"""Tests for the geocoding service."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services.geocoding import (
    GeocodingResult,
    geocode_location_name,
    haversine_meters,
    is_within_caracas,
)

# -- Unit tests for pure functions --


class TestIsWithinCaracas:
    def test_inside(self):
        assert is_within_caracas(10.48, -66.90) is True

    def test_outside_north(self):
        assert is_within_caracas(10.60, -66.90) is False

    def test_outside_east(self):
        assert is_within_caracas(10.48, -66.70) is False

    def test_on_boundary(self):
        assert is_within_caracas(10.55, -66.90) is True


class TestHaversineMeters:
    def test_same_point(self):
        assert haversine_meters(10.48, -66.90, 10.48, -66.90) == pytest.approx(
            0.0, abs=0.01
        )

    def test_known_distance(self):
        # Plaza Bolívar (10.5069, -66.9147) to Teatro Teresa Carreño (10.4983, -66.9182)
        dist = haversine_meters(10.5069, -66.9147, 10.4983, -66.9182)
        assert 800 < dist < 1200  # ~1km


# -- Async tests for geocode_location_name --


def _mock_geoapify_response(
    lat: float = 10.5069,
    lon: float = -66.9147,
    formatted: str = "Plaza Bolívar, Caracas",
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "results": [
            {
                "lat": lat,
                "lon": lon,
                "formatted": formatted,
                "rank": {"confidence": confidence},
            }
        ]
    }


@pytest.mark.asyncio
class TestGeocodeLocationName:
    async def test_success(self):
        mock_response = MagicMock()
        mock_response.json.return_value = _mock_geoapify_response()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.geocoding.httpx.AsyncClient", return_value=mock_client
        ):
            result = await geocode_location_name("Plaza Bolívar", "fake-key")

        assert result is not None
        assert isinstance(result, GeocodingResult)
        assert result.lat == pytest.approx(10.5069)
        assert result.lng == pytest.approx(-66.9147)
        assert result.formatted_address == "Plaza Bolívar, Caracas"
        assert result.confidence == pytest.approx(0.9)

    async def test_no_results(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.geocoding.httpx.AsyncClient", return_value=mock_client
        ):
            result = await geocode_location_name("Nonexistent Place", "fake-key")

        assert result is None

    async def test_http_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.geocoding.httpx.AsyncClient", return_value=mock_client
        ):
            result = await geocode_location_name("Some Place", "fake-key")

        assert result is None

    async def test_outside_caracas(self):
        mock_response = MagicMock()
        mock_response.json.return_value = _mock_geoapify_response(
            lat=40.7128,
            lon=-74.0060,  # New York
        )
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.geocoding.httpx.AsyncClient", return_value=mock_client
        ):
            result = await geocode_location_name("Statue of Liberty", "fake-key")

        assert result is None

    async def test_with_address(self):
        mock_response = MagicMock()
        mock_response.json.return_value = _mock_geoapify_response()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.geocoding.httpx.AsyncClient", return_value=mock_client
        ):
            result = await geocode_location_name(
                "Teatro Teresa Carreño", "fake-key", address="Av. Urdaneta, Caracas"
            )

        assert result is not None
        # Verify the search text used the address
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert "Av. Urdaneta" in params["text"]
