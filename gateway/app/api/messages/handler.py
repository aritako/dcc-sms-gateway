from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid7

from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing import BillingService
from app.core.encoding import EncodingService
from app.core.quote import Quote, QuoteService, UnknownDestination
from app.db.models import Message, MessageEvent, MessageStatus, Tenant

from .models import BatchMessage, BatchResponse, MessagesRequest


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

  async def accept_batch(self, tenant: Tenant, items: list[MessagesRequest]) -> BatchResponse:
    """Quote every item, then write all messages, events and charges.

    Quoting reads only, so an unpriced destination is reported before any write.
    The caller commits; on any exception it must roll back.
    """
    quotes: list[Quote] = []
    errors: list[ItemError] = []
    for index, item in enumerate(items):
      try:
        quotes.append(await self.get_quote(text=item.text, to=item.to, tenant=tenant))
      except UnknownDestination:
        errors.append(ItemError(index, "to", "UNSUPPORTED_DESTINATION", f"no price for {item.to}"))
    if errors:
      raise InvalidBatch(errors)

    batch_id = uuid7()
    message_ids = [uuid7() for _ in items]
    rows = [
      {
        "id": message_id,
        "tenant_id": tenant.id,
        "batch_id": batch_id,
        "to_number": item.to,
        "sender_id": item.from_,
        "text": item.text,
        "client_ref": item.client_ref,
        "encoding": quote.encoding,
        "segments": quote.segments,
        "price_per_segment_cents": quote.price_per_segment_cents,
        "total_price_cents": quote.total_price_cents,
        "status": MessageStatus.ACCEPTED,
      }
      for message_id, item, quote in zip(message_ids, items, quotes, strict=True)
    ]
    # Bulk inserts: one statement per table, not one per message. Messages first,
    # because events and ledger rows reference them.
    await self.db.execute(insert(Message), rows)
    await self.db.execute(
      insert(MessageEvent),
      [
        {"id": uuid7(), "message_id": message_id, "status": MessageStatus.ACCEPTED}
        for message_id in message_ids
      ],
    )
    charges = [
      (message_id, quote.total_price_cents)
      for message_id, quote in zip(message_ids, quotes, strict=True)
    ]
    await BillingService(self.db).charge_many(tenant.id, charges)

    return BatchResponse(
      batch_id=batch_id,
      messages=[
        BatchMessage(
          id=message_id,
          status=MessageStatus.ACCEPTED,
          client_ref=item.client_ref,
          encoding=quote.encoding,
          segments=quote.segments,
          price_per_segment=quote.price_per_segment_cents,
          total_price=quote.total_price_cents,
        )
        for message_id, item, quote in zip(message_ids, items, quotes, strict=True)
      ],
      total_price=sum(quote.total_price_cents for quote in quotes),
      currency=tenant.currency,
    )

  def invalid_batch_response(self, errors: list[ItemError]) -> JSONResponse:
    return JSONResponse(
      status_code=422,
      content={"error": "INVALID_BATCH_ITEM", "errors": [asdict(error) for error in errors]},
    )

  def error_response(self, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})
