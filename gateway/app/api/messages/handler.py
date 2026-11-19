import secrets
from uuid import UUID

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encoding import EncodingService
from app.core.quote import Quote, QuoteService
from app.db.models import ApiKey, Tenant
from app.security import hash_api_key
from app.settings import get_settings


class MessagesHandler:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def tenant_for_api_key(self, api_key: str) -> Tenant | None:
    """Return the tenant that owns `api_key`, or None when the key is unknown."""
    if not api_key:
      return None
    return await self.db.scalar(
      select(Tenant)
      .join(ApiKey, ApiKey.tenant_id == Tenant.id)
      .where(ApiKey.key_hash == hash_api_key(api_key))
    )

  def admin_key_matches(self, admin_key: str) -> bool:
    # compare_digest raises TypeError on non-ASCII str; bytes compare is total.
    return secrets.compare_digest(
      admin_key.encode("utf-8"), get_settings().admin_api_key.encode("utf-8")
    )

  async def tenant_by_id(self, tenant_id: UUID) -> Tenant | None:
    return await self.db.get(Tenant, tenant_id)

  async def get_quote(self, text: str, to: str, tenant: Tenant) -> Quote:
    service = QuoteService(self.db, EncodingService(), tenant.currency)
    return await service.quote(text, to)

  def error_response(self, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})
