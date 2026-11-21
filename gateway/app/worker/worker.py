import asyncio
import logging

import httpx

from app.db.database import dispose_engine, get_sessionmaker
from app.db.redis import close_redis, get_redis
from app.settings import get_settings

from .dispatch import Dispatcher
from .limiter import CarrierRateLimiter


async def main() -> None:
  logging.basicConfig(level=logging.INFO)
  settings = get_settings()
  limiter = CarrierRateLimiter(redis=get_redis(), limit=settings.carrier_submit_rate)
  carrier_client = httpx.AsyncClient(
    base_url=settings.carrier_url, timeout=settings.carrier_timeout_seconds
  )

  dispatcher = Dispatcher(get_sessionmaker(), carrier_client, limiter, settings)
  logging.info("worker started: %s submits/s across all workers", settings.carrier_submit_rate)
  try:
    await dispatcher.run(idle_seconds=settings.worker_idle_seconds)
  finally:
    await carrier_client.aclose()
    await close_redis()
    await dispose_engine()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
