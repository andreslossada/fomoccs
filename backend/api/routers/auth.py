from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy import func, select

from api.dependencies import (
    ALGORITHM,
    CurrentUserDep,
    SessionDep,
    _bearer_scheme,
    create_access_token,
    hash_password,
    verify_api_key,
    verify_password,
)
from api.middleware.rate_limit import (
    admin_rate_limit,
    login_rate_limit,
    register_rate_limit,
)
from api.middleware.token_blocklist import add_to_blocklist
from api.models.user import User
from api.schemas.auth import AuthResponse
from api.schemas.user import UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_OptionalCreds = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserCreate,
    db: SessionDep,
    _rate: None = Depends(register_rate_limit),
) -> AuthResponse:
    existing = await db.scalar(select(User).where(User.email == data.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=data.email,
        display_name=data.display_name,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    await db.commit()

    token = create_access_token(user.id)
    return AuthResponse(token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    data: UserLogin,
    db: SessionDep,
    _rate: None = Depends(login_rate_limit),
) -> AuthResponse:
    user = await db.scalar(select(User).where(User.email == data.email))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user.last_login_at = func.now()
    await db.commit()

    token = create_access_token(user.id)
    return AuthResponse(token=token, user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    _user: CurrentUserDep,
    credentials: _OptionalCreds,
) -> Response:
    """Logout: add the token's jti to the blocklist so it cannot be reused."""
    if credentials is not None:
        try:
            payload = jwt.decode(
                credentials.credentials,
                "",
                algorithms=[ALGORITHM],
                options={"verify_signature": False},
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                exp_dt = datetime.fromtimestamp(exp, tz=UTC)
                add_to_blocklist(jti, exp_dt)
        except Exception:
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/promote-admin", status_code=status.HTTP_200_OK)
async def promote_admin(
    email: str,
    db: SessionDep,
    _api_key: None = Depends(verify_api_key),
    _rate: None = Depends(admin_rate_limit),
) -> dict[str, str]:
    """Promote a user to admin. Secured by SYNC_API_KEY."""
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.is_admin = True
    await db.commit()
    return {"message": f"{email} is now an admin"}
