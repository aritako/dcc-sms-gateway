import secrets
from uuid import UUID, uuid7

from dataclasses import dataclass
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from app.api.messages.models import MessagesRequest
from app.core.billing import BillingService
from app.core.encoding import EncodingService
from app.core.quote import Quote, QuoteService
from app.db.models import Message, MessageEvent, MessageStatus, Tenant
from app.settings import get_settings


@dataclass(frozen=True, slots=True)
class ItemError:
  index: int
  field: str
  code: str
  message: str

class InvalidBatch(Exception):
  """At least one batch item is invalid."""

  def __init__(self, errors: list[ItemError]) -> None:
    super().__init__(f"{len(errors)} invalid batch item(s)")
    self.errors = errors

class MessagesHandler:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

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

  async def accept_message(self, tenant: Tenant, payload: MessagesRequest) -> tuple[Message, Quote]:
    """Write the ACCEPTED message, its event and the charge in one transaction.

    The caller commits. Errors propagate untouched so the controller can map
    them to status codes; the request session rolls back when it closes.
    """
    quote = await self.get_quote(text=payload.text, to=payload.to, tenant=tenant)

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
      status=MessageStatus.ACCEPTED,
    )
    self.db.add(message)
    self.db.add(MessageEvent(message_id=message.id, status=MessageStatus.ACCEPTED))
    await self.db.flush()

    await BillingService(self.db).charge(
      tenant_id=tenant.id, message_id=message.id, amount_cents=quote.total_price_cents
    )
    return message, quote
  
  def parse_batch(self, raw_items: list[dict[str, Any]]) -> list[MessagesRequest]:
    """Validate every item with the single-send rules; report all failures by index."""
    items: list[MessagesRequest] = []
    errors: list[ItemError] = []
    for index, raw in enumerate(raw_items):
      try:
        items.append(MessagesRequest.model_validate(raw))
      except ValidationError as error:
        errors.extend(
          ItemError(
            index=index,
            field=".".join(str(part) for part in detail["loc"]),
            code=detail["type"].upper(),
            message=detail["msg"],
          )
          for detail in error.errors()
        )
    if errors:
      raise InvalidBatch(errors)
    return items
  
  def accept_batch(self, tenant: Tenant, batch: list[MessagesRequest]):
    pass
  
  def error_response(self, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})

