from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.config import get_settings

settings = get_settings()

# Supabase's session-mode pooler caps clients per tenant (typically 15).
# Keep our own pool small so concurrent requests and Celery workers share
# that budget instead of each exhausting it. pool_pre_ping evicts stale
# connections the pooler may have reaped while idle.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_recycle=280,
    connect_args={"ssl": "prefer"} if "supabase" in settings.db_host else {},
)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
