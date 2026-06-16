import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.database import get_db
from api.middleware.token_blocklist import is_blocked
from api.models.user import User

SessionDep = Annotated[AsyncSession, Depends(get_db)]

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    result: str = pwd_context.hash(password)
    return result


def verify_password(plain_password: str, hashed_password: str) -> bool:
    result: bool = pwd_context.verify(plain_password, hashed_password)
    return result


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    token: str = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return token


def _decode_token(token: str) -> int | None:
    """Decode a JWT and return the user_id, or None if invalid.

    Accepts tokens without ``exp`` (legacy) for backward compatibility.
    For tokens that have ``exp``, checks the claim manually so that
    tokens missing it are still accepted.  Also rejects tokens whose
    ``jti`` has been added to the blocklist (e.g. via logout).
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
        exp = payload.get("exp")
        if exp is not None:
            exp_dt = datetime.fromtimestamp(exp, tz=UTC)
            if datetime.now(UTC) >= exp_dt:
                return None

        jti = payload.get("jti")
        if jti is not None and is_blocked(jti):
            return None

        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (JWTError, ValueError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> User:
    """Require a valid JWT. Returns the User or raises 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    user_id = _decode_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_optional_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> User | None:
    """Extract user from JWT if present; return None otherwise."""
    if credentials is None:
        return None

    user_id = _decode_token(credentials.credentials)
    if user_id is None:
        return None

    user: User | None = await db.scalar(select(User).where(User.id == user_id))
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
OptionalUserDep = Annotated[User | None, Depends(get_optional_user)]


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the current user to be an admin. Raises 403 if not."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


AdminUserDep = Annotated[User, Depends(get_admin_user)]


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


async def get_geoapify_key() -> str:
    """Return the Geoapify API key or 503 if not configured."""
    key = get_settings().geoapify_api_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Geocoding service not configured",
        )
    return key


GeoapifyKeyDep = Annotated[str, Depends(get_geoapify_key)]


class GeocodingKeys:
    """Container for the configured geocoding provider keys.

    Google is primary; Geoapify is a free safety net. Either may be
    unconfigured; the flow handles missing keys gracefully.
    """

    def __init__(self, google_api_key: str, geoapify_api_key: str) -> None:
        self.google_api_key = google_api_key
        self.geoapify_api_key = geoapify_api_key

    def has_any(self) -> bool:
        return bool(self.google_api_key or self.geoapify_api_key)


async def get_geocoding_keys() -> GeocodingKeys:
    """Return configured geocoding provider keys, or 503 if none."""
    settings = get_settings()
    keys = GeocodingKeys(
        google_api_key=settings.google_maps_api_key,
        geoapify_api_key=settings.geoapify_api_key,
    )
    if not keys.has_any():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Geocoding service not configured",
        )
    return keys


GeocodingKeyDep = Annotated[GeocodingKeys, Depends(get_geocoding_keys)]


# ---------------------------------------------------------------------------
# API key verification (shared between admin / pipeline endpoints)
# ---------------------------------------------------------------------------


async def verify_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Verify the API key from header (preferred) or query param (legacy)."""
    key = x_api_key or request.query_params.get("api_key")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key required via X-API-Key header or api_key query parameter",
        )
    if key != get_settings().sync_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
