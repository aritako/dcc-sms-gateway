import asyncio

from redis.asyncio import Redis

SLOT_MS = 50

class CarrierRateLimiter:
  """Global carrier limit shared by every worker through one Redis counter per slot.

  Each 50 ms slot, numbered on the Redis clock, has `limit * 50 // 1000` permits
  (2 at 45/s). Any 1-second span touches at most 21 slots, so it holds at most
  42 submits, safely under the carrier's 50. Redis errors propagate to the
  caller, which must fail closed (not submit).
  """

  def __init__(self, redis: Redis, limit: int) -> None:
    self.redis = redis
    self.per_slot = limit * SLOT_MS // 1000
    if self.per_slot < 1:
      raise ValueError(f"limit must be at least {1000 // SLOT_MS}/s")

  async def acquire(self) -> None:
    """Wait for a permit, sleeping until the next slot when this one is full."""
    while True:
      seconds, microseconds = await self.redis.time()  # one clock for all workers
      now_ms = seconds * 1000 + microseconds // 1000
      slot = now_ms // SLOT_MS
      key = f"ratelimit:carrier:submit:{slot}"
      async with self.redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.pexpire(key, 2 * SLOT_MS)  # old slots clean themselves up
        taken, _ = await pipe.execute()
      if taken <= self.per_slot:
        return
      await asyncio.sleep(((slot + 1) * SLOT_MS - now_ms) / 1000)