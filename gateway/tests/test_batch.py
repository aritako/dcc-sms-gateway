import asyncio
from uuid import UUID

import pytest
from helpers import (
  assert_nothing_written,
  balance_of,
  batch_items,
  count,
  create_tenant,
  send_batch,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey, LedgerEntry, LedgerKind, Message, MessageEvent


async def test_batch_of_1000_is_accepted_and_charged_per_message(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000_000)

  response = await send_batch(client, items=batch_items(1_000))

  assert response.status_code == 202
  body = response.json()
  assert body["total_price"] == 3_000
  assert body["currency"] == "USD"
  UUID(body["batch_id"])
  assert len(body["messages"]) == 1_000
  assert len({m["id"] for m in body["messages"]}) == 1_000
  assert body["messages"][0] == {
    "id": body["messages"][0]["id"],
    "status": "ACCEPTED",
    "client_ref": "ref-0",
    "encoding": "GSM7",
    "segments": 1,
    "price_per_segment": 3,
    "total_price": 3,
  }
  assert body["messages"][-1]["client_ref"] == "ref-999"

  assert await count(session, Message) == 1_000
  assert await count(session, MessageEvent) == 1_000
  assert await count(session, LedgerEntry) == 1_000
  batch_ids = (await session.scalars(select(Message.batch_id).distinct())).all()
  assert [str(batch_id) for batch_id in batch_ids] == [body["batch_id"]]

  balance = await balance_of(session, tenant)
  ledger_sum = await session.scalar(
    select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.tenant_id == tenant.id)
  )
  assert balance == 1_000_000 - 3_000
  assert balance == tenant.opening_balance_cents + ledger_sum
  # One CHARGE row per message; the last row carries the tenant's final balance.
  charges = (
    await session.scalars(
      select(LedgerEntry.amount_cents).where(LedgerEntry.kind == LedgerKind.CHARGE)
    )
  ).all()
  assert charges == [-3] * 1_000
  lowest = await session.scalar(select(func.min(LedgerEntry.balance_after_cents)))
  assert lowest == balance


async def test_insufficient_aggregate_balance_is_402_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=29)  # 10 messages need 30

  response = await send_batch(client, items=batch_items(10), idempotency_key="broke-batch")

  assert response.status_code == 402
  assert response.json() == {"error": "INSUFFICIENT_BALANCE"}
  await assert_nothing_written(session, tenant, 29)
  assert await session.get(IdempotencyKey, (tenant.id, "broke-batch")) is None


async def test_empty_batch_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session)

  response = await send_batch(client, items=[])

  assert response.status_code == 422
  assert response.json() == {"error": "BATCH_EMPTY"}
  await assert_nothing_written(session, tenant, 10_000)


async def test_batch_of_1001_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session)

  response = await send_batch(client, items=batch_items(1_001))

  assert response.status_code == 422
  assert response.json() == {"error": "BATCH_TOO_LARGE"}
  await assert_nothing_written(session, tenant, 10_000)


@pytest.mark.parametrize(
  ("override", "field"),
  [
    ({"to": "8801712345678"}, "to"),
    ({"text": ""}, "text"),
    ({"from": "ACME-SMS-1234"}, "from"),
    ({"to": "+15551234567"}, "to"),  # well-formed but unpriced
  ],
)
async def test_one_invalid_item_is_422_and_writes_nothing(
  client: AsyncClient,
  session: AsyncSession,
  override: dict[str, str],
  field: str,
) -> None:
  tenant = await create_tenant(session)
  items = batch_items(5)
  items[3] = {**items[3], **override}

  response = await send_batch(client, items=items)

  assert response.status_code == 422
  body = response.json()
  assert body["error"] == "INVALID_BATCH_ITEM"
  (error,) = body["errors"]
  assert set(error) == {"index", "field", "code", "message"}
  assert error["index"] == 3
  assert error["field"] == field
  assert error["message"]
  await assert_nothing_written(session, tenant, 10_000)


async def test_replay_returns_the_same_ids_and_charges_once(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000)

  first = await send_batch(client, items=batch_items(3), idempotency_key="batch-key")
  second = await send_batch(client, items=batch_items(3), idempotency_key="batch-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert second.json() == first.json()
  assert await count(session, Message) == 3
  assert await count(session, MessageEvent) == 3
  assert await count(session, LedgerEntry) == 3
  assert await balance_of(session, tenant) == 1_000 - 9


async def test_concurrent_duplicate_batches_charge_once(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000)

  responses = await asyncio.gather(
    *(send_batch(client, items=batch_items(3), idempotency_key="racing-batch") for _ in range(5))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["batch_id"] for response in responses}) == 1
  assert (
    len({tuple(message["id"] for message in response.json()["messages"]) for response in responses})
    == 1
  )
  assert await count(session, Message) == 3
  assert await count(session, MessageEvent) == 3
  assert await count(session, LedgerEntry) == 3
  assert await balance_of(session, tenant) == 1_000 - 9
