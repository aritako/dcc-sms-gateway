import asyncio
import logging

from app.settings import get_settings
from app.db.redis import get_redis, close_redis
from app.db.database import dispose_engine, get_sessionmaker
from .limiter import CarrierRateLimiter
from .dispatch import Dispatcher

import httpx

async def main():
  logging.basicConfig(level=logging.INFO)
  settings = get_settings()
  limiter = CarrierRateLimiter(redis=get_redis(), limit=settings.carrier_submit_rate)
  carrierClient = httpx.AsyncClient(base_url=settings.carrier_url, timeout=settings.carrier_timeout_seconds)

  dispatcher = Dispatcher(get_sessionmaker(), carrierClient, limiter, settings)
  logging.info("worker started: %s submits/s across all workers", settings.carrier_submit_rate)
  try:
    await dispatcher.run(idle_seconds=settings.worker_idle_seconds)
  finally:
    await close_redis()
    await dispose_engine()
    await carrierClient.aclose()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    pass
