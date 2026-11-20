from uuid import UUID

from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.database import get_session
from app.db.models import ApiKey, Tenant
from app.security import admin_key_matches, hash_api_key


async def tenant_for_api_key(db: AsyncSession, api_key: str) -> Tenant | None:
  """Return the tenant that owns `api_key`, or None when the key is unknown."""
  if not api_key:
    return None
  return await db.scalar(
    select(Tenant)
    .join(ApiKey, ApiKey.tenant_id == Tenant.id)
    .where(ApiKey.key_hash == hash_api_key(api_key))
  )


async def read_scope(
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
  admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
  tenant_id: UUID | None = Query(default=None),
) -> Tenant | None:
  """Resolve the read headers into the tenant to query, or None for all tenants.

  The admin key decides alone when present: a wrong one is 401 even if a valid
  tenant key is also sent. A tenant key can never name a tenant (403), and an
  admin naming a tenant that does not exist gets 404.
  """
  if admin_key is not None:
    if not admin_key_matches(admin_key):
      raise ApiError(401, "UNAUTHORIZED")
    if tenant_id is None:
      return None
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
      raise ApiError(404, "NOT_FOUND")
    return tenant

  tenant = await tenant_for_api_key(db, api_key or "")
  if tenant is None:
    raise ApiError(401, "UNAUTHORIZED")
  if tenant_id is not None:
    raise ApiError(403, "FORBIDDEN")
  return tenant


async def tenant_read_scope(tenant: Tenant | None = Depends(read_scope)) -> Tenant:
  """A read that is meaningless across tenants, like a balance."""
  if tenant is None:
    raise ApiError(422, "TENANT_ID_REQUIRED")
  return tenant
