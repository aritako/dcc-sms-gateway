import secrets
from uuid import UUID, uuid7

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.messages.models import MessagesRequest
from app.core.billing import BillingService
from app.core.encoding import EncodingService
from app.core.quote import Quote, QuoteService
from app.db.models import ApiKey, Message, MessageEvent, MessageStatus, Tenant
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

  async def accept_message(self, tenant: Tenant, payload: MessagesRequest):
    # Immediately accepts a message, should return a 200
    # An async worker will be responsible for the delivery 
    
    quote = self.get_quote(text=payload.text,to=payload.to, tenant=tenant)

    message = Message(
      id=uuid7(),  # needed now for the events; the column default only fires at flush
      tenant_id=tenant.id,
      to_number=payload.to,
      sender_id=payload.from_,
      text=payload.text,
      client_ref=payload.client_ref,
      encoding=quote.encoding,
      segments=quote.segments,
      price_per_segment_cents=quote.price_per_segment_cents,
      total_price_cents=quote.total_price_cents,
      status=MessageStatus.QUEUED,
    )
    self.db.add(message)
    self.db.add_all(
      [
        MessageEvent(message_id=message.id, status=MessageStatus.ACCEPTED),
        MessageEvent(message_id=message.id, status=MessageStatus.QUEUED),
      ]
    )
    await self.db.flush()

    billingService = BillingService(self.db)
    await billingService.charge(
      tenant_id=tenant.id, message_id=message.id, amount_cents=quote.total_price_cents
    )
    return message, quote

  def error_response(self, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})
