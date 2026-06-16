from datetime import UTC, datetime
from typing import Any

from api.config import get_settings


def _redis_client() -> Any:
    import redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]


def add_to_blocklist(jti: str, exp: datetime) -> None:
    """Add a JWT ``jti`` to the blocklist, auto-expiring at ``exp``."""
    try:
        r = _redis_client()
        ttl = max(1, int((exp - datetime.now(UTC)).total_seconds()))
        r.setex(f"bl:{jti}", ttl, "1")
    except Exception:
        pass


def is_blocked(jti: str) -> bool:
    """Return ``True`` if the ``jti`` is in the blocklist."""
    try:
        r = _redis_client()
        return bool(r.exists(f"bl:{jti}"))
    except Exception:
        return False  # Redis unavailable — fail open
