from settings import settings
from redis.asyncio import Redis

REDIS_URL = settings.redis_url

redis = Redis.from_url(REDIS_URL)