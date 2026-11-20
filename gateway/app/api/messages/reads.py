import uuid
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.models import Message, MessageEvent, MessageStatus, Tenant

from .models import (
  MessageDetail,
  MessageEventOut,
  MessagesPage,
  MessageSummary,
)


def _summary(message: Message, tenant_name: str, currency: str) -> MessageSummary:
  return MessageSummary(
    id=message.id,
    tenant_id=message.tenant_id,
    tenant_name=tenant_name,
    to=message.to_number,
    from_=message.sender_id,
    status=message.status,
    segments=message.segments,
    price_per_segment=message.price_per_segment_cents,
    total_price=message.total_price_cents,
    currency=currency,
    client_ref=message.client_ref,
    batch_id=message.batch_id,
    created_at=message.created_at,
  )


class MessagesReader:
  """Tenant-scoped reads over messages and their events."""

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  def _select_messages(self, tenant: Tenant | None) -> Select[Message, str, str]:
    statement = select(Message, Tenant.name, Tenant.currency).join(
      Tenant, Tenant.id == Message.tenant_id
    )
    if tenant is not None:
      statement = statement.where(Message.tenant_id == tenant.id)
    return statement

  async def list_messages(
    self,
    tenant: Tenant | None,
    status: MessageStatus | None = None,
    client_ref: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
  ) -> MessagesPage:
    statement = self._select_messages(tenant)
    if status is not None:
      statement = statement.where(Message.status == status.value)
    if client_ref is not None:
      statement = statement.where(Message.client_ref == client_ref)
    if created_after is not None:
      statement = statement.where(Message.created_at >= created_after)
    if created_before is not None:
      statement = statement.where(Message.created_at <= created_before)
    # Newest first, with the uuid7 id as a stable tie-breaker.
    statement = (
      statement.order_by(Message.created_at.desc(), Message.id.desc())
      .limit(limit + 1)  # one extra row tells us whether another page exists
      .offset(offset)
    )

    rows = (await self.db.execute(statement)).all()
    has_more = len(rows) > limit
    return MessagesPage(
      messages=[
        _summary(message, tenant_name, currency) for message, tenant_name, currency in rows[:limit]
      ],
      limit=limit,
      offset=offset,
      has_more=has_more,
    )

  async def get_message(self, tenant: Tenant | None, message_id: uuid.UUID) -> MessageDetail:
    statement = self._select_messages(tenant).where(Message.id == message_id)
    row = (await self.db.execute(statement)).one_or_none()
    if row is None:
      # Another tenant's message and a missing one are indistinguishable.
      raise ApiError(404, "NOT_FOUND")

    message, tenant_name, currency = row
    events = (
      await self.db.scalars(
        select(MessageEvent)
        .where(MessageEvent.message_id == message.id)
        .order_by(MessageEvent.created_at, MessageEvent.id)
      )
    ).all()

    summary = _summary(message, tenant_name, currency)
    return MessageDetail(
      **summary.model_dump(),
      text=message.text,
      encoding=message.encoding,
      carrier=message.carrier,
      carrier_id=message.carrier_id,
      updated_at=message.updated_at,
      events=[
        MessageEventOut(id=event.id, status=event.status, created_at=event.created_at)
        for event in events
      ],
    )
