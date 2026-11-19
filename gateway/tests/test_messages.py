import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import (
  ApiKey,
  LedgerEntry,
  LedgerKind,
  Message,
  MessageEvent,
  MessageStatus,
  Price,
  Tenant,
)
from app.security import hash_api_key

API_KEY = "dcc_test_api_key"
MESSAGE = {"to": "+8801712345678", "from": "ACME", "text": "hello"}
SEND_PATH = "/v1/messages"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
  async for session in get_session():
    yield session


async def _create_tenant(session: AsyncSession, *, balance_cents: int = 10_000) -> Tenant:
  tenant = Tenant(
    name="Test Tenant",
    opening_balance_cents=balance_cents,
    balance_cents=balance_cents,
  )
  session.add(tenant)
  await session.flush()
  session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(API_KEY)))
  # "hello" is one GSM-7 segment, so every send in this file costs 3 cents.
  session.add(Price(prefix="+880", price_per_segment_cents=3))
  await session.commit()
  return tenant


@pytest.fixture
async def tenant(session: AsyncSession) -> Tenant:
  return await _create_tenant(session)


async def _send(
  client: AsyncClient,
  *,
  key: str | None = API_KEY,
  **overrides: object,
) -> Response:
  return await client.post(
    SEND_PATH,
    json={**MESSAGE, **overrides},
    headers={"X-API-Key": key} if key is not None else {},
  )


async def _count(session: AsyncSession, model: type) -> int:
  return await session.scalar(select(func.count()).select_from(model))


async def _balance(session: AsyncSession, tenant: Tenant) -> int:
  return await session.scalar(select(Tenant.balance_cents).where(Tenant.id == tenant.id))


async def test_missing_key_is_unauthorized(client: AsyncClient) -> None:
  response = await client.post(SEND_PATH, json=MESSAGE)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_unknown_key_is_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await _send(client, key="not-a-real-key")

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_valid_send_returns_202_and_the_quote(client: AsyncClient, tenant: Tenant) -> None:
  response = await _send(client, client_ref="order-42")

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
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  body = (await _send(client)).json()

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

  assert await _balance(session, tenant) == 9_997


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
  tenant: Tenant,
  override: dict[str, str],
  field: str,
) -> None:
  response = await _send(client, **override)

  assert response.status_code == 422
  assert any(field in error["loc"] for error in response.json()["detail"])
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_unknown_destination_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  response = await _send(client, to="+15551234567")

  assert response.status_code == 422
  assert response.json() == {"error": "UNSUPPORTED_DESTINATION"}
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_insufficient_balance_is_402_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await _create_tenant(session, balance_cents=2)

  response = await _send(client)

  assert response.status_code == 402
  assert response.json() == {"error": "INSUFFICIENT_BALANCE"}
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 2


async def test_concurrent_sends_cannot_overdraw(client: AsyncClient, session: AsyncSession) -> None:
  tenant = await _create_tenant(session, balance_cents=30)  # exactly 10 x 3 cents

  responses = await asyncio.gather(*(_send(client) for _ in range(50)))

  codes = Counter(response.status_code for response in responses)
  assert codes == {202: 10, 402: 40}
  assert await _balance(session, tenant) == 0
  assert await _count(session, Message) == 10
  assert await _count(session, MessageEvent) == 10
  assert await _count(session, LedgerEntry) == 10
  charged = await session.scalar(select(func.sum(LedgerEntry.amount_cents)))
  assert charged == -30
