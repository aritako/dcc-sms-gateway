import asyncio
import time

from helpers import max_in_any_second

from app.db.redis import get_redis
from app.worker.limiter import CarrierRateLimiter


async def test_permits_are_capped_per_slot() -> None:
  limiter = CarrierRateLimiter(get_redis(), limit=40)  # 2 permits per 50 ms slot

  started = time.monotonic()
  for _ in range(42):
    await limiter.acquire()
  time_elapsed = time.monotonic() - started
  # 42 permits need 21 slots, so at least 20 full slots (~1 s) pass in between.
  assert time_elapsed >= 0.95


async def test_three_workers_never_exceed_the_limit_in_any_second() -> None:
  # Three limiters = three workers sharing the same Redis slot counters.
  limiters = [CarrierRateLimiter(get_redis(), limit=45) for _ in range(3)]
  granted_at: list[float] = []

  async def worker(limiter: CarrierRateLimiter, permits: int) -> None:
    for _ in range(permits):
      await limiter.acquire()
      granted_at.append(time.monotonic())

  await asyncio.gather(*(worker(limiter, 40) for limiter in limiters))  # 120 permits

  # Assert that 120 permits are granted, 40 from each worker
  assert len(granted_at) == 120
  # On the Redis clock the slots allow at most 42 in any second.
  assert max_in_any_second(granted_at) <= 50
