from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
  AsyncEngine,
  AsyncSession,
  async_sessionmaker,
  create_async_engine,
)

from app.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
  """Create the engine on first use so imports never need a live database."""
  global _engine
  if _engine is None:
    _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
  return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
  """Session factory bound to the lazily created engine."""
  global _session_factory
  if _session_factory is None:
    _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
  return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
  """FastAPI dependency: one session per request, closed when the request ends.

  Commits are left to the caller so a request that charges money decides when
  the transaction ends; anything uncommitted is rolled back on close.
  """
  async with get_sessionmaker()() as session:
    yield session


async def dispose_engine() -> None:
  """Release pooled connections (app shutdown, tests, seed script)."""
  global _engine, _session_factory
  if _engine is not None:
    await _engine.dispose()
  _engine = None
  _session_factory = None
