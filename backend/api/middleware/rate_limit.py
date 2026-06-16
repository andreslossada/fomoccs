from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, status

from api.config import get_settings


def _redis_client() -> Any:
    """Lazy Redis connection from the configured REDIS_URL."""
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]


async def _rate_limit(
    request: Request,
    max_requests: int,
    window_seconds: int,
) -> None:
    """Block clients exceeding *max_requests* per *window_seconds*."""
    try:
        r = _redis_client()
    except Exception:
        return

    forwarded = request.headers.get("X-Forwarded-For")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else request.client.host if request.client else "unknown"
    )

    key = f"rl:{request.url.path}:{ip}"
    try:
        current = r.incr(key)
        if current == 1:
            r.expire(key, window_seconds)
        if current > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )
    except HTTPException:
        raise
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pre-configured rate-limit dependencies
# ---------------------------------------------------------------------------


def _make_limiter(
    max_requests: int, window_seconds: int
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    async def _limiter(request: Request) -> None:
        await _rate_limit(request, max_requests, window_seconds)

    return _limiter


login_rate_limit = _make_limiter(max_requests=5, window_seconds=60)
register_rate_limit = _make_limiter(max_requests=3, window_seconds=3600)
admin_rate_limit = _make_limiter(max_requests=5, window_seconds=60)
feed_rate_limit = _make_limiter(max_requests=60, window_seconds=60)
