"""Tests for the locations router."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.dependencies import create_access_token, get_geoapify_key, hash_password
from api.models.event import Event
from api.models.location import Location, LocationAlternateName, LocationTag
from api.models.tag import Tag
from api.models.user import User
from api.routers.locations import router
from api.services.geocoding import GeocodingResult


def _make_app(
    db_session: AsyncSession, *, geoapify_key: str = "test-api-key"
) -> FastAPI:
    """Create a minimal FastAPI app with the locations router for testing."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _override_get_db():
        yield db_session

    async def _override_geoapify_key() -> str:
        return geoapify_key

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_geoapify_key] = _override_geoapify_key
    return app


@pytest.fixture(autouse=True)
def _mock_celery_send_task():
    """Prevent Celery from trying to connect to Redis during tests."""
    with patch("api.routers.locations.celery") as mock_celery:
        mock_celery.send_task = MagicMock()
        yield mock_celery


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def sample_user(db_session: AsyncSession) -> User:
    """Create a user for authenticated requests."""
    user = User(
        email="testuser@example.com",
        display_name="Test User",
        password_hash=hash_password("testpass123"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def auth_headers(sample_user: User) -> dict[str, str]:
    """Return Authorization header dict for the sample_user."""
    token = create_access_token(sample_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Create an admin user for authenticated requests."""
    user = User(
        email="admin@example.com",
        display_name="Admin User",
        password_hash=hash_password("adminpass123"),
        is_admin=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_headers(admin_user: User) -> dict[str, str]:
    """Return Authorization header dict for the admin_user."""
    token = create_access_token(admin_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def sample_location(db_session: AsyncSession) -> Location:
    """Create a single location in the DB."""
    location = Location(
        name="Museum of Modern Art",
        short_name="MoMA",
        very_short_name="MoMA",
        address="11 W 53rd St, New York",
        description="A famous museum.",
        lat=40.7614,
        lng=-73.9776,
        emoji="x",
    )
    db_session.add(location)
    await db_session.flush()
    return location


@pytest_asyncio.fixture
async def sample_location_with_event(
    db_session: AsyncSession, sample_location: Location
) -> Location:
    """Create a location that has an event referencing it."""
    event = Event(
        name="Art Exhibition",
        location_id=sample_location.id,
    )
    db_session.add(event)
    await db_session.flush()
    return sample_location


# ---------------------------------------------------------------------------
# List locations
# ---------------------------------------------------------------------------


class TestListLocations:
    @pytest.mark.asyncio
    async def test_list_locations_empty(self, client: AsyncClient) -> None:
        # Act
        resp = await client.get("/api/v1/locations/")

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_list_locations_returns_items(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # Arrange
        loc_b = Location(name="Brooklyn Museum")
        loc_a = Location(name="American Museum")
        db_session.add_all([loc_b, loc_a])
        await db_session.flush()

        # Act
        resp = await client.get("/api/v1/locations/")

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = [item["name"] for item in body["data"]]
        assert names == ["American Museum", "Brooklyn Museum"]

    @pytest.mark.asyncio
    async def test_list_locations_includes_event_count(
        self, client: AsyncClient, sample_location_with_event: Location
    ) -> None:
        # Act
        resp = await client.get("/api/v1/locations/")

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["event_count"] == 1

    @pytest.mark.asyncio
    async def test_list_locations_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # Arrange — create 3 locations
        for name in ["Alpha", "Beta", "Gamma"]:
            db_session.add(Location(name=name))
        await db_session.flush()

        # Act — fetch with limit=2, offset=0
        resp1 = await client.get("/api/v1/locations/", params={"limit": 2, "offset": 0})
        # Act — fetch with limit=2, offset=2
        resp2 = await client.get("/api/v1/locations/", params={"limit": 2, "offset": 2})

        # Assert
        body1 = resp1.json()
        body2 = resp2.json()
        assert body1["total"] == 3
        assert len(body1["data"]) == 2
        assert body2["total"] == 3
        assert len(body2["data"]) == 1
        # All three names covered
        all_names = [i["name"] for i in body1["data"]] + [
            i["name"] for i in body2["data"]
        ]
        assert sorted(all_names) == ["Alpha", "Beta", "Gamma"]


# ---------------------------------------------------------------------------
# Get location detail
# ---------------------------------------------------------------------------


class TestGetLocationDetail:
    @pytest.mark.asyncio
    async def test_get_location_returns_detail(
        self, client: AsyncClient, db_session: AsyncSession, sample_location: Location
    ) -> None:
        # Arrange — add alternate name and tag
        db_session.add(
            LocationAlternateName(
                location_id=sample_location.id, alternate_name="MoMA NYC"
            )
        )
        tag = Tag(name="art")
        db_session.add(tag)
        await db_session.flush()
        db_session.add(LocationTag(location_id=sample_location.id, tag_id=tag.id))
        await db_session.flush()

        # Act
        resp = await client.get(f"/api/v1/locations/{sample_location.id}")

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == sample_location.id
        assert body["name"] == "Museum of Modern Art"
        assert len(body["alternate_names"]) == 1
        assert body["alternate_names"][0]["alternate_name"] == "MoMA NYC"
        assert len(body["tags"]) == 1
        assert body["tags"][0]["name"] == "art"

    @pytest.mark.asyncio
    async def test_get_location_not_found(self, client: AsyncClient) -> None:
        # Act
        resp = await client.get("/api/v1/locations/99999")

        # Assert
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Create location
# ---------------------------------------------------------------------------


class TestCreateLocation:
    @pytest.mark.asyncio
    async def test_create_location_success(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Arrange
        payload = {
            "name": "New Gallery",
            "short_name": "NG",
            "address": "123 Art St",
            "lat": 40.0,
            "lng": -74.0,
        }

        # Act
        resp = await client.post(
            "/api/v1/locations/", json=payload, headers=auth_headers
        )

        # Assert
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "New Gallery"
        assert body["short_name"] == "NG"
        assert body["address"] == "123 Art St"
        assert body["lat"] == 40.0
        assert body["lng"] == -74.0
        assert "id" in body
        assert "created_at" in body

    @pytest.mark.asyncio
    async def test_create_location_with_tags_and_alt_names(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Arrange
        payload = {
            "name": "Tagged Venue",
            "alternate_names": ["TV", "The Venue"],
            "tags": ["music", "nightlife"],
        }

        # Act
        resp = await client.post(
            "/api/v1/locations/", json=payload, headers=auth_headers
        )

        # Assert
        assert resp.status_code == 201
        body = resp.json()
        alt_names = [a["alternate_name"] for a in body["alternate_names"]]
        assert "TV" in alt_names
        assert "The Venue" in alt_names
        tag_names = [t["name"] for t in body["tags"]]
        assert "music" in tag_names
        assert "nightlife" in tag_names

    @pytest.mark.asyncio
    async def test_create_location_persists_website_url_and_type(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Arrange
        payload = {
            "name": "Typed Venue",
            "website_url": "https://example.com",
            "type": "area",
        }

        # Act
        resp = await client.post(
            "/api/v1/locations/", json=payload, headers=auth_headers
        )

        # Assert
        assert resp.status_code == 201
        body = resp.json()
        assert body["website_url"] == "https://example.com"
        assert body["type"] == "area"

    @pytest.mark.asyncio
    async def test_create_location_requires_auth(self, client: AsyncClient) -> None:
        # Arrange
        payload = {"name": "Unauthorized Venue"}

        # Act
        resp = await client.post("/api/v1/locations/", json=payload)

        # Assert
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Update location
# ---------------------------------------------------------------------------


class TestUpdateLocation:
    @pytest.mark.asyncio
    async def test_update_location_partial(
        self,
        client: AsyncClient,
        sample_location: Location,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange — only update the name
        payload = {"name": "Updated Museum Name"}

        # Act
        resp = await client.put(
            f"/api/v1/locations/{sample_location.id}",
            json=payload,
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Updated Museum Name"
        # Other fields remain unchanged
        assert body["short_name"] == "MoMA"
        assert body["address"] == "11 W 53rd St, New York"

    @pytest.mark.asyncio
    async def test_update_location_replaces_tags(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        sample_location: Location,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange — give the location an initial tag
        tag = Tag(name="old-tag")
        db_session.add(tag)
        await db_session.flush()
        db_session.add(LocationTag(location_id=sample_location.id, tag_id=tag.id))
        await db_session.flush()

        # Act — update with new tags
        payload = {"tags": ["new-tag-a", "new-tag-b"]}
        resp = await client.put(
            f"/api/v1/locations/{sample_location.id}",
            json=payload,
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        tag_names = sorted(t["name"] for t in body["tags"])
        assert tag_names == ["new-tag-a", "new-tag-b"]

    @pytest.mark.asyncio
    async def test_update_location_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        resp = await client.put(
            "/api/v1/locations/99999",
            json={"name": "Ghost"},
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_location_requires_auth(
        self, client: AsyncClient, sample_location: Location
    ) -> None:
        # Act
        resp = await client.put(
            f"/api/v1/locations/{sample_location.id}",
            json={"name": "No Auth"},
        )

        # Assert
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Delete location
# ---------------------------------------------------------------------------


class TestDeleteLocation:
    @pytest.mark.asyncio
    async def test_delete_location_success(
        self,
        client: AsyncClient,
        sample_location: Location,
        auth_headers: dict[str, str],
    ) -> None:
        # Act
        resp = await client.delete(
            f"/api/v1/locations/{sample_location.id}",
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = await client.get(f"/api/v1/locations/{sample_location.id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_location_with_events_succeeds(
        self,
        client: AsyncClient,
        sample_location_with_event: Location,
        auth_headers: dict[str, str],
    ) -> None:
        # Act — soft delete allows deleting locations with associated events
        resp = await client.delete(
            f"/api/v1/locations/{sample_location_with_event.id}",
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_location_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        resp = await client.delete(
            "/api/v1/locations/99999",
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_location_requires_auth(
        self, client: AsyncClient, sample_location: Location
    ) -> None:
        # Act
        resp = await client.delete(f"/api/v1/locations/{sample_location.id}")

        # Assert
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Soft-delete behavior
# ---------------------------------------------------------------------------


class TestSoftDeleteLocation:
    @pytest.mark.asyncio
    async def test_delete_location_is_soft_delete(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_location: Location,
    ) -> None:
        # Act
        resp = await client.delete(
            f"/api/v1/locations/{sample_location.id}", headers=auth_headers
        )
        assert resp.status_code == 204

        # Assert — record still exists with deleted_at set
        await db_session.refresh(sample_location)
        assert sample_location.deleted_at is not None

    @pytest.mark.asyncio
    async def test_list_locations_excludes_deleted_by_default(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        # Arrange
        loc1 = Location(name="Active Location")
        loc2 = Location(name="Deleted Location")
        db_session.add_all([loc1, loc2])
        await db_session.flush()

        resp = await client.delete(f"/api/v1/locations/{loc2.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act
        resp = await client.get("/api/v1/locations/")
        body = resp.json()

        # Assert
        names = [item["name"] for item in body["data"]]
        assert "Active Location" in names
        assert "Deleted Location" not in names

    @pytest.mark.asyncio
    async def test_list_locations_includes_deleted_when_requested(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        # Arrange
        loc1 = Location(name="Active Location 2")
        loc2 = Location(name="Deleted Location 2")
        db_session.add_all([loc1, loc2])
        await db_session.flush()

        resp = await client.delete(f"/api/v1/locations/{loc2.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act
        resp = await client.get("/api/v1/locations/", params={"include_deleted": True})
        body = resp.json()

        # Assert
        names = [item["name"] for item in body["data"]]
        assert "Active Location 2" in names
        assert "Deleted Location 2" in names

    @pytest.mark.asyncio
    async def test_get_deleted_location_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_location: Location,
    ) -> None:
        # Arrange
        resp = await client.delete(
            f"/api/v1/locations/{sample_location.id}", headers=auth_headers
        )
        assert resp.status_code == 204

        # Act
        resp = await client.get(f"/api/v1/locations/{sample_location.id}")

        # Assert
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_event_count_excludes_soft_deleted_events(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        # Arrange — location with 2 events, soft-delete one
        loc = Location(name="Event Count Location")
        db_session.add(loc)
        await db_session.flush()

        e1 = Event(name="Active Event", location_id=loc.id)
        e2 = Event(name="Deleted Event", location_id=loc.id)
        db_session.add_all([e1, e2])
        await db_session.flush()

        e2.soft_delete()
        await db_session.flush()

        # Act
        resp = await client.get("/api/v1/locations/")
        body = resp.json()

        # Assert — only 1 active event counted
        loc_data = next(
            item for item in body["data"] if item["name"] == "Event Count Location"
        )
        assert loc_data["event_count"] == 1


# ---------------------------------------------------------------------------
# Bulk create locations
# ---------------------------------------------------------------------------

_MOCK_GEO_RESULT = GeocodingResult(
    lat=10.5069, lng=-66.9147, formatted_address="Plaza Bolívar, Caracas", confidence=0.9
)


class TestBulkCreateLocations:
    @pytest.mark.asyncio
    async def test_bulk_create_success(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {
            "locations": [
                {"name": "Venue A"},
                {"name": "Venue B"},
            ]
        }
        with patch(
            "api.routers.locations.geocode_location_name",
            new_callable=AsyncMock,
            return_value=_MOCK_GEO_RESULT,
        ):
            resp = await client.post(
                "/api/v1/locations/bulk", json=payload, headers=auth_headers
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["total"] == 2
        assert body["created"] == 2
        assert body["errors"] == 0
        assert len(body["results"]) == 2
        for item in body["results"]:
            assert item["status"] == "created"
            assert item["location"]["lat"] == pytest.approx(10.5069)

    @pytest.mark.asyncio
    async def test_bulk_create_with_existing_coords(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {"locations": [{"name": "With Coords", "lat": 10.48, "lng": -66.90}]}
        mock_geocode = AsyncMock(return_value=_MOCK_GEO_RESULT)
        with patch("api.routers.locations.geocode_location_name", mock_geocode):
            resp = await client.post(
                "/api/v1/locations/bulk", json=payload, headers=auth_headers
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["results"][0]["location"]["lat"] == pytest.approx(10.48)
        # Should not have called geocode since coords were provided
        mock_geocode.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_create_max_50_enforced(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {"locations": [{"name": f"Venue {i}"} for i in range(51)]}
        resp = await client.post(
            "/api/v1/locations/bulk", json=payload, headers=auth_headers
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_create_partial_geocode_failure(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {"locations": [{"name": "Good"}, {"name": "Bad"}]}

        call_count = 0

        async def _mock_geocode(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _MOCK_GEO_RESULT
            return None

        with patch(
            "api.routers.locations.geocode_location_name",
            side_effect=_mock_geocode,
        ):
            resp = await client.post(
                "/api/v1/locations/bulk", json=payload, headers=auth_headers
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["created"] == 2  # both created, one without coords
        statuses = [r["status"] for r in body["results"]]
        assert "created" in statuses
        assert "geocode_failed" in statuses

    @pytest.mark.asyncio
    async def test_bulk_create_requires_auth(self, client: AsyncClient) -> None:
        payload = {"locations": [{"name": "No Auth"}]}
        resp = await client.post("/api/v1/locations/bulk", json=payload)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Geocode single location
# ---------------------------------------------------------------------------


class TestGeocodeLocation:
    @pytest.mark.asyncio
    async def test_geocode_success(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_headers: dict[str, str],
    ) -> None:
        loc = Location(name="No Coords Venue")
        db_session.add(loc)
        await db_session.flush()

        with patch(
            "api.routers.locations.geocode_location_name",
            new_callable=AsyncMock,
            return_value=_MOCK_GEO_RESULT,
        ):
            resp = await client.post(
                f"/api/v1/locations/{loc.id}/geocode", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["geocoded"] is True
        assert body["lat"] == pytest.approx(10.5069)
        assert body["lng"] == pytest.approx(-66.9147)

    @pytest.mark.asyncio
    async def test_geocode_already_has_coords(
        self,
        client: AsyncClient,
        sample_location: Location,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.post(
            f"/api/v1/locations/{sample_location.id}/geocode",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["geocoded"] is True
        assert body["lat"] == pytest.approx(40.7614)

    @pytest.mark.asyncio
    async def test_geocode_force_overwrites(
        self,
        client: AsyncClient,
        sample_location: Location,
        auth_headers: dict[str, str],
    ) -> None:
        mock_geocode = AsyncMock(return_value=_MOCK_GEO_RESULT)
        with patch("api.routers.locations.geocode_location_name", mock_geocode):
            resp = await client.post(
                f"/api/v1/locations/{sample_location.id}/geocode?force=true",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["geocoded"] is True
        assert body["lat"] == pytest.approx(10.5069)
        mock_geocode.assert_called_once()

    @pytest.mark.asyncio
    async def test_geocode_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/api/v1/locations/99999/geocode", headers=auth_headers
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_geocode_no_result(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_headers: dict[str, str],
    ) -> None:
        loc = Location(name="Unknown Place")
        db_session.add(loc)
        await db_session.flush()

        with patch(
            "api.routers.locations.geocode_location_name",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await client.post(
                f"/api/v1/locations/{loc.id}/geocode", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["geocoded"] is False
        assert body["lat"] is None

    @pytest.mark.asyncio
    async def test_geocode_requires_auth(
        self, client: AsyncClient, sample_location: Location
    ) -> None:
        resp = await client.post(f"/api/v1/locations/{sample_location.id}/geocode")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Backfill geocode
# ---------------------------------------------------------------------------


class TestBackfillGeocode:
    @pytest.mark.asyncio
    async def test_backfill_geocodes_missing(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_headers: dict[str, str],
    ) -> None:
        loc_no_coords = Location(name="Missing Coords")
        loc_with_coords = Location(name="Has Coords", lat=10.48, lng=-66.90)
        db_session.add_all([loc_no_coords, loc_with_coords])
        await db_session.flush()

        with patch(
            "api.routers.locations.geocode_location_name",
            new_callable=AsyncMock,
            return_value=_MOCK_GEO_RESULT,
        ):
            resp = await client.post(
                "/api/v1/locations/backfill-geocode", headers=admin_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_processed"] >= 1
        assert body["geocoded"] >= 1
        assert body["skipped"] == 0

    @pytest.mark.asyncio
    async def test_backfill_empty(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_headers: dict[str, str],
    ) -> None:
        loc = Location(name="Complete", lat=10.48, lng=-66.90)
        db_session.add(loc)
        await db_session.flush()

        resp = await client.post(
            "/api/v1/locations/backfill-geocode", headers=admin_headers
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_processed"] == 0

    @pytest.mark.asyncio
    async def test_backfill_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/locations/backfill-geocode")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_backfill_requires_admin(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/api/v1/locations/backfill-geocode", headers=auth_headers
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_backfill_respects_limit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_headers: dict[str, str],
    ) -> None:
        # Create 3 locations without coords
        for i in range(3):
            db_session.add(Location(name=f"No Coords {i}"))
        await db_session.flush()

        with patch(
            "api.routers.locations.geocode_location_name",
            new_callable=AsyncMock,
            return_value=_MOCK_GEO_RESULT,
        ):
            resp = await client.post(
                "/api/v1/locations/backfill-geocode?limit=2",
                headers=admin_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_processed"] == 2
