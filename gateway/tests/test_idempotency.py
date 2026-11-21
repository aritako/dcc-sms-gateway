import asyncio

import pytest
from helpers import (
  balance_of,
  count,
  create_tenant,
  send_message,
  top_up,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey, LedgerEntry, Message, MessageEvent


async def test_same_key_and_body_replays_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  first = await send_message(client, idempotency_key="same-key")
  second = await send_message(client, idempotency_key="same-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert second.json() == first.json()
  assert await count(session, Message) == 1
  assert await count(session, MessageEvent) == 1
  assert await count(session, LedgerEntry) == 1
  assert await balance_of(session, tenant) == 9_997


@pytest.mark.parametrize("key", ["", "k" * 256])
async def test_invalid_idempotency_key_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant, key: str
) -> None:
  response = await send_message(client, idempotency_key=key)

  assert response.status_code == 422
  assert await count(session, Message) == 0
  assert await count(session, MessageEvent) == 0
  assert await count(session, LedgerEntry) == 0
  assert await balance_of(session, tenant) == 10_000


async def test_same_key_with_different_body_is_409_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  first = await send_message(client, idempotency_key="reused-key")
  second = await send_message(client, idempotency_key="reused-key", text="different")

  assert first.status_code == 202
  assert second.status_code == 409
  assert second.json() == {"error": "IDEMPOTENCY_KEY_REUSED"}
  assert await count(session, Message) == 1
  assert await count(session, MessageEvent) == 1
  assert await count(session, LedgerEntry) == 1
  assert await balance_of(session, tenant) == 9_997


async def test_concurrent_duplicates_create_one_message_and_one_charge(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  responses = await asyncio.gather(
    *(send_message(client, idempotency_key="racing-key") for _ in range(10))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["id"] for response in responses}) == 1
  assert await count(session, Message) == 1
  assert await count(session, MessageEvent) == 1
  assert await count(session, LedgerEntry) == 1
  assert await balance_of(session, tenant) == 9_997


async def test_concurrent_duplicates_replay_even_when_the_balance_runs_out(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=3)  # room for exactly one send

  responses = await asyncio.gather(
    *(send_message(client, idempotency_key="last-cents") for _ in range(5))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["id"] for response in responses}) == 1
  assert await count(session, Message) == 1
  assert await balance_of(session, tenant) == 0


async def test_same_key_is_independent_per_tenant(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="Other Tenant", api_key="other-key")

  first = await send_message(client, idempotency_key="shared-key")
  second = await send_message(client, key="other-key", idempotency_key="shared-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert first.json()["id"] != second.json()["id"]
  assert await count(session, Message) == 2
  assert await count(session, MessageEvent) == 2
  assert await count(session, LedgerEntry) == 2
  assert await balance_of(session, tenant) == 9_997
  assert await balance_of(session, other) == 9_997


async def test_402_is_not_stored_and_the_same_key_works_after_top_up(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=2)

  rejected = await send_message(client, idempotency_key="retry-key")
  assert rejected.status_code == 402
  assert await count(session, Message) == 0
  assert await count(session, LedgerEntry) == 0
  assert await session.get(IdempotencyKey, (tenant.id, "retry-key")) is None

  await top_up(session, tenant, 998)

  accepted = await send_message(client, idempotency_key="retry-key")
  assert accepted.status_code == 202
  assert await count(session, Message) == 1
  # The top-up CREDIT and the send CHARGE.
  assert await count(session, LedgerEntry) == 2
  assert await balance_of(session, tenant) == 997
  ledger_sum = await session.scalar(
    select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.tenant_id == tenant.id)
  )
  assert ledger_sum == 995
