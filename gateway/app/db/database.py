from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.settings import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
  """Create the engine on first use so imports never need a live database."""
  global _engine
  if _engine is None:
    _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
  return _engine


async def dispose_engine() -> None:
  """Release pooled connections (app shutdown, tests, seed script)."""
  global _engine
  if _engine is not None:
    await _engine.dispose()
  _engine = None
