from redis.asyncio import Redis

from app.settings import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    """Create the client on first use so imports never need a live Redis."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close pooled connections (app shutdown, tests)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None
