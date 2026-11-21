import asyncio
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

import pytest
from helpers import assert_nothing_written, balance_of, count, create_tenant, send_message
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
  LedgerEntry,
  LedgerKind,
  Message,
  MessageEvent,
  MessageStatus,
)


async def test_valid_send_returns_202_and_the_quote(client: AsyncClient, tenant) -> None:
  response = await send_message(client, client_ref="order-42")

  assert response.status_code == 202
  body = response.json()
  assert body == {
    "id": body["id"],
    "status": "ACCEPTED",
    "client_ref": "order-42",
    "encoding": "GSM7",
    "segments": 1,
    "price_per_segment": 3,
    "total_price": 3,
    "currency": "USD",
  }
  UUID(body["id"])


async def test_send_writes_message_event_and_charge(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  body = (await send_message(client)).json()

  message = await session.get(Message, UUID(body["id"]))
  assert message is not None
  assert message.tenant_id == tenant.id
  assert message.status == MessageStatus.ACCEPTED
  assert message.total_price_cents == 3
  # Claimable immediately: the row itself is the queue.
  assert message.available_at is not None
  assert message.available_at <= datetime.now(UTC)

  statuses = (
    await session.scalars(select(MessageEvent.status).where(MessageEvent.message_id == message.id))
  ).all()
  assert statuses == [MessageStatus.ACCEPTED.value]

  charge = await session.scalar(select(LedgerEntry).where(LedgerEntry.message_id == message.id))
  assert charge is not None
  assert charge.kind == LedgerKind.CHARGE.value
  assert charge.amount_cents == -3
  assert charge.balance_after_cents == 9_997

  assert await balance_of(session, tenant) == 9_997


@pytest.mark.parametrize(
  ("override", "field"),
  [
    ({"to": "8801712345678"}, "to"),  # missing +
    ({"from": "ACME-SMS-1234"}, "from"),  # not alphanumeric
    ({"text": ""}, "text"),
  ],
)
async def test_invalid_payload_is_422_and_writes_nothing(
  client: AsyncClient,
  session: AsyncSession,
  tenant,
  override: dict[str, str],
  field: str,
) -> None:
  response = await send_message(client, **override)

  assert response.status_code == 422
  assert any(field in error["loc"] for error in response.json()["detail"])
  await assert_nothing_written(session, tenant, 10_000)


async def test_unknown_destination_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  response = await send_message(client, to="+15551234567")

  assert response.status_code == 422
  assert response.json() == {"error": "UNSUPPORTED_DESTINATION"}
  await assert_nothing_written(session, tenant, 10_000)


async def test_insufficient_balance_is_402_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=2)

  response = await send_message(client)

  assert response.status_code == 402
  assert response.json() == {"error": "INSUFFICIENT_BALANCE"}
  await assert_nothing_written(session, tenant, 2)


async def test_concurrent_sends_cannot_overdraw(client: AsyncClient, session: AsyncSession) -> None:
  tenant = await create_tenant(session, balance_cents=30)  # exactly 10 x 3 cents

  responses = await asyncio.gather(*(send_message(client) for _ in range(50)))

  codes = Counter(response.status_code for response in responses)
  assert codes == {202: 10, 402: 40}
  assert await balance_of(session, tenant) == 0
  assert await count(session, Message) == 10
  assert await count(session, MessageEvent) == 10
  assert await count(session, LedgerEntry) == 10
  charged = await session.scalar(select(func.sum(LedgerEntry.amount_cents)))
  assert charged == -30
