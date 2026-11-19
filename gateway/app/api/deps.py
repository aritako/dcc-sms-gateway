from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Tenant
from app.security import hash_api_key


async def tenant_for_api_key(db: AsyncSession, api_key: str) -> Tenant | None:
  """Return the tenant that owns `api_key`, or None when the key is unknown."""
  if not api_key:
    return None
  return await db.scalar(
    select(Tenant)
    .join(ApiKey, ApiKey.tenant_id == Tenant.id)
    .where(ApiKey.key_hash == hash_api_key(api_key))
  )
