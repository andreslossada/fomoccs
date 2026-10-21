"""Tests for the auth router."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.dependencies import create_access_token, hash_password
from api.models.user import User
from api.routers.auth import router


def _make_app(db_session: AsyncSession) -> FastAPI:
    """Create a minimal FastAPI app with the auth router for testing."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def registered_user(db_session: AsyncSession) -> User:
    """Create a user with a properly hashed password for login tests."""
    user = User(
        email="existing@example.com",
        display_name="Existing User",
        password_hash=hash_password("securepass123"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


class TestRegisterHappyPath:
    @pytest.mark.asyncio
    async def test_returns_201_with_token_and_user(self, client):
        # Arrange
        payload = {
            "email": "new@example.com",
            "password": "Strongpass1",
            "display_name": "New User",
        }

        # Act
        resp = await client.post("/api/v1/auth/register", json=payload)

        # Assert
        assert resp.status_code == 201
        body = resp.json()
        assert "token" in body
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["display_name"] == "New User"
        assert "id" in body["user"]
        assert "created_at" in body["user"]

    @pytest.mark.asyncio
    async def test_register_without_display_name(self, client):
        # Arrange
        payload = {
            "email": "nodisplay@example.com",
            "password": "Strongpass1",
        }

        # Act
        resp = await client.post("/api/v1/auth/register", json=payload)

        # Assert
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["display_name"] is None


class TestRegisterValidation:
    @pytest.mark.asyncio
    async def test_duplicate_email_returns_409(self, client, registered_user):
        # Arrange
        payload = {
            "email": "existing@example.com",
            "password": "Anotherpass1",
        }

        # Act
        resp = await client.post("/api/v1/auth/register", json=payload)

        # Assert
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_short_password_returns_422(self, client):
        # Arrange
        payload = {
            "email": "short@example.com",
            "password": "short",
        }

        # Act
        resp = await client.post("/api/v1/auth/register", json=payload)

        # Assert
        assert resp.status_code == 422


class TestLoginHappyPath:
    @pytest.mark.asyncio
    async def test_returns_200_with_token_and_user(self, client, registered_user):
        # Arrange
        payload = {
            "email": "existing@example.com",
            "password": "securepass123",
        }

        # Act
        resp = await client.post("/api/v1/auth/login", json=payload)

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert body["user"]["email"] == "existing@example.com"
        assert body["user"]["id"] == registered_user.id


class TestLoginErrors:
    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, client, registered_user):
        # Arrange
        payload = {
            "email": "existing@example.com",
            "password": "wrongpassword1",
        }

        # Act
        resp = await client.post("/api/v1/auth/login", json=payload)

        # Assert
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_email_returns_401(self, client):
        # Arrange
        payload = {
            "email": "nobody@example.com",
            "password": "doesntmatter1",
        }

        # Act
        resp = await client.post("/api/v1/auth/login", json=payload)

        # Assert
        assert resp.status_code == 401


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_with_auth_returns_204(self, client, registered_user):
        # Arrange
        token = create_access_token(registered_user.id)

        # Act
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Assert
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_logout_without_auth_returns_401(self, client):
        # Act
        resp = await client.post("/api/v1/auth/logout")

        # Assert
        assert resp.status_code == 401


class TestMe:
    @pytest.mark.asyncio
    async def test_me_with_auth_returns_200(self, client, registered_user):
        # Arrange
        token = create_access_token(registered_user.id)

        # Act
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "existing@example.com"
        assert body["id"] == registered_user.id

    @pytest.mark.asyncio
    async def test_me_without_auth_returns_401(self, client):
        # Act
        resp = await client.get("/api/v1/auth/me")

        # Assert
        assert resp.status_code == 401
