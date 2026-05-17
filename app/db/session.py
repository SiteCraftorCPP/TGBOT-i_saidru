from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

_engines: dict[str, AsyncEngine] = {}


def make_engine(settings: Settings) -> AsyncEngine:
    key = settings.database_url
    if key not in _engines:
        _engines[key] = create_async_engine(
            key,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engines[key]


def make_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(make_engine(settings), expire_on_commit=False)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
