from collections.abc import AsyncIterator
from datetime import datetime
import uuid

from sqlalchemy import URL, DateTime, MetaData, func, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def database_url(raw: str | None = None) -> URL:
    """Accepts the URLs hosted Postgres providers hand out (e.g. Neon's
    `postgres://…?sslmode=require&channel_binding=require`) and converts them for asyncpg."""
    url = make_url(raw or get_settings().database_url)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and sslmode not in ("disable", "allow", "prefer") and "ssl" not in query:
        query["ssl"] = "require"
    if get_settings().vercel:
        # Serverless: connections may sit behind a transaction-mode pooler, so don't cache
        # prepared statements.
        query["prepared_statement_cache_size"] = "0"
    return url.set(query=query)


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if get_settings().vercel:
            # Function instances freeze between requests; don't keep idle connections around.
            _engine = create_async_engine(database_url(), poolclass=NullPool, connect_args={"statement_cache_size": 0})
        else:
            _engine = create_async_engine(database_url(), pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
