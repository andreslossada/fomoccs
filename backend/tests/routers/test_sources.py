"""Tests for the sources router."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.dependencies import create_access_token, hash_password
from api.models.source import CrawlConfig, Source, SourceUrl
from api.models.user import User
from api.routers.sources import router

PREFIX = "/api/v1/sources"


def _make_app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def sample_user(db_session: AsyncSession) -> User:
    user = User(
        email="sources-test@example.com",
        display_name="Sources Tester",
        password_hash=hash_password("password123"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def auth_headers(sample_user: User) -> dict[str, str]:
    token = create_access_token(sample_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def sample_source(db_session: AsyncSession) -> Source:
    source = Source(name="Test Source", type="crawler")
    db_session.add(source)
    await db_session.flush()
    return source


@pytest_asyncio.fixture
async def sample_source_with_url(
    db_session: AsyncSession, sample_source: Source
) -> Source:
    url = SourceUrl(source_id=sample_source.id, url="https://example.com", sort_order=0)
    db_session.add(url)
    await db_session.flush()
    return sample_source


# ---------------------------------------------------------------------------
# List sources
# ---------------------------------------------------------------------------


class TestListSources:
    @pytest.mark.asyncio
    async def test_list_sources_empty(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.get(f"{PREFIX}/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_list_sources_returns_items(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        resp = await client.get(f"{PREFIX}/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_sources_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{PREFIX}/")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Get source detail
# ---------------------------------------------------------------------------


class TestGetSource:
    @pytest.mark.asyncio
    async def test_get_source_returns_detail(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        resp = await client.get(f"{PREFIX}/{sample_source.id}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Test Source"
        assert body["type"] == "crawler"

    @pytest.mark.asyncio
    async def test_get_source_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.get(f"{PREFIX}/99999", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Create source
# ---------------------------------------------------------------------------


class TestCreateSource:
    @pytest.mark.asyncio
    async def test_create_source_minimal(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {"name": "New Source", "type": "crawler"}
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "New Source"
        assert body["type"] == "crawler"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_create_source_with_urls_and_config(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        payload = {
            "name": "Full Source",
            "type": "api",
            "urls": [{"url": "https://example.com/api"}],
            "crawl_config": {
                "crawl_frequency": 12,
                "crawl_mode": "json_api",
            },
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert len(body["urls"]) == 1
        assert body["crawl_config"] is not None
        assert body["crawl_config"]["crawl_frequency"] == 12

    @pytest.mark.asyncio
    async def test_create_source_requires_auth(self, client: AsyncClient) -> None:
        payload = {"name": "No Auth", "type": "crawler"}
        resp = await client.post(f"{PREFIX}/", json=payload)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Update source
# ---------------------------------------------------------------------------


class TestUpdateSource:
    @pytest.mark.asyncio
    async def test_update_source_partial(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        payload = {"name": "Updated Source"}
        resp = await client.put(
            f"{PREFIX}/{sample_source.id}", json=payload, headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Updated Source"

    @pytest.mark.asyncio
    async def test_update_source_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.put(
            f"{PREFIX}/99999", json={"name": "Ghost"}, headers=auth_headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete source
# ---------------------------------------------------------------------------


class TestDeleteSource:
    @pytest.mark.asyncio
    async def test_delete_source_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        resp = await client.delete(f"{PREFIX}/{sample_source.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = await client.get(
            f"{PREFIX}/{sample_source.id}", headers=auth_headers
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_source_not_found(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        resp = await client.delete(f"{PREFIX}/99999", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Crawl config upsert
# ---------------------------------------------------------------------------


class TestCrawlConfig:
    @pytest.mark.asyncio
    async def test_create_crawl_config(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        payload = {"crawl_frequency": 24, "crawl_mode": "browser"}
        resp = await client.put(
            f"{PREFIX}/{sample_source.id}/config",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["crawl_frequency"] == 24
        assert body["crawl_mode"] == "browser"

    @pytest.mark.asyncio
    async def test_update_existing_crawl_config(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source: Source,
    ) -> None:
        # Arrange -- create initial config
        config = CrawlConfig(
            source_id=sample_source.id,
            crawl_frequency=24,
            crawl_mode="browser",
        )
        db_session.add(config)
        await db_session.flush()

        # Act -- update
        payload = {"crawl_frequency": 12}
        resp = await client.put(
            f"{PREFIX}/{sample_source.id}/config",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["crawl_frequency"] == 12


# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------


class TestSourceUrls:
    @pytest.mark.asyncio
    async def test_add_source_url(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        payload = {"url": "https://new-url.com"}
        resp = await client.post(
            f"{PREFIX}/{sample_source.id}/urls",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        # The schema normalizes a bare host to a trailing slash.
        assert body["url"] == "https://new-url.com/"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_delete_source_url(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source: Source,
    ) -> None:
        # Arrange
        url = SourceUrl(
            source_id=sample_source.id, url="https://to-delete.com", sort_order=0
        )
        db_session.add(url)
        await db_session.flush()

        # Act
        resp = await client.delete(
            f"{PREFIX}/{sample_source.id}/urls/{url.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_source_url_not_found(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        resp = await client.delete(
            f"{PREFIX}/{sample_source.id}/urls/99999",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Soft-delete behavior
# ---------------------------------------------------------------------------


class TestSoftDeleteSource:
    @pytest.mark.asyncio
    async def test_delete_source_is_soft_delete(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source: Source,
    ) -> None:
        # Act
        resp = await client.delete(f"{PREFIX}/{sample_source.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Assert — record still exists in DB with deleted_at set
        await db_session.refresh(sample_source)
        assert sample_source.deleted_at is not None

    @pytest.mark.asyncio
    async def test_list_sources_excludes_deleted_by_default(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        # Arrange — create 2 sources, delete one
        s1 = Source(name="Active Source", type="crawler")
        s2 = Source(name="Deleted Source", type="crawler")
        db_session.add_all([s1, s2])
        await db_session.flush()

        resp = await client.delete(f"{PREFIX}/{s2.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act
        resp = await client.get(f"{PREFIX}/", headers=auth_headers)
        body = resp.json()

        # Assert
        names = [item["name"] for item in body["data"]]
        assert "Active Source" in names
        assert "Deleted Source" not in names

    @pytest.mark.asyncio
    async def test_list_sources_includes_deleted_when_requested(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        # Arrange
        s1 = Source(name="Active Source 2", type="crawler")
        s2 = Source(name="Deleted Source 2", type="crawler")
        db_session.add_all([s1, s2])
        await db_session.flush()

        resp = await client.delete(f"{PREFIX}/{s2.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act
        resp = await client.get(
            f"{PREFIX}/", headers=auth_headers, params={"include_deleted": True}
        )
        body = resp.json()

        # Assert
        names = [item["name"] for item in body["data"]]
        assert "Active Source 2" in names
        assert "Deleted Source 2" in names

    @pytest.mark.asyncio
    async def test_get_deleted_source_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        # Arrange — delete the source
        resp = await client.delete(f"{PREFIX}/{sample_source.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act
        resp = await client.get(f"{PREFIX}/{sample_source.id}", headers=auth_headers)

        # Assert
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_source_url_is_soft_delete(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source: Source,
    ) -> None:
        # Arrange
        url = SourceUrl(
            source_id=sample_source.id, url="https://soft-delete.com", sort_order=0
        )
        db_session.add(url)
        await db_session.flush()

        # Act
        resp = await client.delete(
            f"{PREFIX}/{sample_source.id}/urls/{url.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Assert — record still exists with deleted_at set
        await db_session.refresh(url)
        assert url.deleted_at is not None

    @pytest.mark.asyncio
    async def test_soft_deleted_url_allows_reuse(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        # Arrange — create source with URL, then soft-delete the URL
        s1 = Source(name="Original Source", type="crawler")
        db_session.add(s1)
        await db_session.flush()
        url = SourceUrl(source_id=s1.id, url="https://reuse-me.com", sort_order=0)
        db_session.add(url)
        await db_session.flush()

        resp = await client.delete(
            f"{PREFIX}/{s1.id}/urls/{url.id}", headers=auth_headers
        )
        assert resp.status_code == 204

        # Act — create new source with the same URL
        payload = {
            "name": "Reuse Source",
            "type": "crawler",
            "urls": [{"url": "https://reuse-me.com"}],
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)

        # Assert
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_delete_already_deleted_source_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source: Source,
    ) -> None:
        # Arrange — delete once
        resp = await client.delete(f"{PREFIX}/{sample_source.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Act — try to delete again
        resp = await client.delete(f"{PREFIX}/{sample_source.id}", headers=auth_headers)

        # Assert
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Duplicate URL prevention
# ---------------------------------------------------------------------------


class TestDuplicateSourceUrls:
    @pytest.mark.asyncio
    async def test_create_source_rejects_duplicate_url(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source_with_url: Source,
    ) -> None:
        # Act — try to create another source with the same URL
        payload = {
            "name": "Duplicate Source",
            "type": "crawler",
            "urls": [{"url": "https://example.com"}],
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)

        # Assert
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_add_url_rejects_duplicate_url(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source_with_url: Source,
    ) -> None:
        # Arrange — create a second source
        s2 = Source(name="Second Source", type="crawler")
        db_session.add(s2)
        await db_session.flush()

        # Act — try to add the same URL to a different source
        payload = {"url": "https://example.com"}
        resp = await client.post(
            f"{PREFIX}/{s2.id}/urls", json=payload, headers=auth_headers
        )

        # Assert
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_add_url_rejects_duplicate_within_same_source(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source_with_url: Source,
    ) -> None:
        # Act — try to add the same URL to the same source
        payload = {"url": "https://example.com"}
        resp = await client.post(
            f"{PREFIX}/{sample_source_with_url.id}/urls",
            json=payload,
            headers=auth_headers,
        )

        # Assert
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_source_rejects_duplicate_urls_within_request(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        # Act — send two identical URLs in one create request
        payload = {
            "name": "Double URL Source",
            "type": "crawler",
            "urls": [
                {"url": "https://same-url.com"},
                {"url": "https://same-url.com"},
            ],
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)

        # Assert
        assert resp.status_code == 409
        assert "Duplicate URLs in request" in resp.json()["detail"]
        assert "https://same-url.com" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_integrity_error_returns_409_on_race_condition(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        sample_source: Source,
    ) -> None:
        """Simulate a race: insert a URL directly in DB, then try via API.

        The Python-level check won't catch it because we bypass
        _check_duplicate_urls by inserting after the check would run.
        The DB unique constraint fires and the IntegrityError handler
        must return 409, not 500.
        """
        # Arrange — insert URL directly so the Python check can't see it
        # (simulates another request committing between check and commit)
        db_session.add(
            SourceUrl(
                source_id=sample_source.id,
                url="https://race-condition.com",
                sort_order=0,
            )
        )
        await db_session.commit()

        # Act — try to create a source with the same URL via the API
        payload = {
            "name": "Racing Source",
            "type": "crawler",
            "urls": [{"url": "https://race-condition.com"}],
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)

        # Assert — should be 409, not 500
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_duplicate_url_error_message_contains_url(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        sample_source_with_url: Source,
    ) -> None:
        # Act
        payload = {
            "name": "Dup Source",
            "type": "crawler",
            "urls": [{"url": "https://example.com"}],
        }
        resp = await client.post(f"{PREFIX}/", json=payload, headers=auth_headers)

        # Assert
        assert resp.status_code == 409
        assert "https://example.com" in resp.json()["detail"]
