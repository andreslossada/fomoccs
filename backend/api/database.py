from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.config import get_settings

settings = get_settings()

# Supabase's session-mode pooler caps clients per tenant (typically 15) and
# two more may be used by the pipeline/Celery processes. Our own pool has to
# stay well under that ceiling or long-running merge passes starve the
# health check and every other request (QueuePool timeout -> HTTP 500).
# pool_pre_ping evicts connections the pooler reaped while idle.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    pool_timeout=120,
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
