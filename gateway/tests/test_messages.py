import asyncio
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

import pytest
from helpers import create_tenant, send_message
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
  IdempotencyKey,
  LedgerEntry,
  LedgerKind,
  Message,
  MessageEvent,
  MessageStatus,
  Tenant,
)

API_KEY = "dcc_test_api_key"
MESSAGE = {"to": "+8801712345678", "from": "ACME", "text": "hello"}
SEND_PATH = "/v1/messages"


@pytest.fixture
async def tenant(session: AsyncSession) -> Tenant:
  return await create_tenant(session)


async def _count(session: AsyncSession, model: type) -> int:
  return await session.scalar(select(func.count()).select_from(model))


async def _balance(session: AsyncSession, tenant: Tenant) -> int:
  return await session.scalar(select(Tenant.balance_cents).where(Tenant.id == tenant.id))


async def _top_up(session: AsyncSession, tenant: Tenant, amount_cents: int) -> None:
  balance_after = await session.scalar(
    update(Tenant)
    .where(Tenant.id == tenant.id)
    .values(balance_cents=Tenant.balance_cents + amount_cents)
    .returning(Tenant.balance_cents)
    .execution_options(synchronize_session=False)
  )
  assert balance_after is not None
  session.add(
    LedgerEntry(
      tenant_id=tenant.id,
      message_id=None,
      kind=LedgerKind.CREDIT.value,
      amount_cents=amount_cents,
      balance_after_cents=balance_after,
    )
  )
  await session.commit()


async def test_missing_key_is_unauthorized(client: AsyncClient) -> None:
  response = await client.post(SEND_PATH, json=MESSAGE)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_unknown_key_is_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await send_message(client, key="not-a-real-key")

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_valid_send_returns_202_and_the_quote(client: AsyncClient, tenant: Tenant) -> None:
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
  client: AsyncClient, session: AsyncSession, tenant: Tenant
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
  response = await send_message(client, **override)

  assert response.status_code == 422
  assert any(field in error["loc"] for error in response.json()["detail"])
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_unknown_destination_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  response = await send_message(client, to="+15551234567")

  assert response.status_code == 422
  assert response.json() == {"error": "UNSUPPORTED_DESTINATION"}
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_insufficient_balance_is_402_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=2)

  response = await send_message(client)

  assert response.status_code == 402
  assert response.json() == {"error": "INSUFFICIENT_BALANCE"}
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 2


async def test_concurrent_sends_cannot_overdraw(client: AsyncClient, session: AsyncSession) -> None:
  tenant = await create_tenant(session, balance_cents=30)  # exactly 10 x 3 cents

  responses = await asyncio.gather(*(send_message(client) for _ in range(50)))

  codes = Counter(response.status_code for response in responses)
  assert codes == {202: 10, 402: 40}
  assert await _balance(session, tenant) == 0
  assert await _count(session, Message) == 10
  assert await _count(session, MessageEvent) == 10
  assert await _count(session, LedgerEntry) == 10
  charged = await session.scalar(select(func.sum(LedgerEntry.amount_cents)))
  assert charged == -30


async def test_same_key_and_body_replays_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  first = await send_message(client, idempotency_key="same-key")
  second = await send_message(client, idempotency_key="same-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert second.json() == first.json()
  assert await _count(session, Message) == 1
  assert await _count(session, MessageEvent) == 1
  assert await _count(session, LedgerEntry) == 1
  assert await _balance(session, tenant) == 9_997


async def test_stored_snapshot_has_status_and_body(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  response = await send_message(client, idempotency_key="snapshot-key")

  stored = await session.get(IdempotencyKey, (tenant.id, "snapshot-key"))
  assert stored is not None
  assert stored.response_status == 202
  assert stored.response_body == response.json()


async def test_oversized_idempotency_key_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  response = await send_message(client, idempotency_key="k" * 256)

  assert response.status_code == 422
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_empty_idempotency_key_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  response = await send_message(client, idempotency_key="")

  assert response.status_code == 422
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == 10_000


async def test_same_key_with_different_body_is_409_and_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  first = await send_message(client, idempotency_key="reused-key")
  second = await send_message(client, idempotency_key="reused-key", text="different")

  assert first.status_code == 202
  assert second.status_code == 409
  assert second.json() == {"error": "IDEMPOTENCY_KEY_REUSED"}
  assert await _count(session, Message) == 1
  assert await _count(session, MessageEvent) == 1
  assert await _count(session, LedgerEntry) == 1
  assert await _balance(session, tenant) == 9_997


async def test_concurrent_duplicates_create_one_message_and_one_charge(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  responses = await asyncio.gather(
    *(send_message(client, idempotency_key="racing-key") for _ in range(10))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["id"] for response in responses}) == 1
  assert await _count(session, Message) == 1
  assert await _count(session, MessageEvent) == 1
  assert await _count(session, LedgerEntry) == 1
  assert await _balance(session, tenant) == 9_997


async def test_concurrent_duplicates_replay_even_when_the_balance_runs_out(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=3)  # room for exactly one send

  responses = await asyncio.gather(
    *(send_message(client, idempotency_key="last-cents") for _ in range(5))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["id"] for response in responses}) == 1
  assert await _count(session, Message) == 1
  assert await _balance(session, tenant) == 0


async def test_same_key_is_independent_per_tenant(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  other = await create_tenant(session, name="Other Tenant", api_key="other-key")

  first = await send_message(client, idempotency_key="shared-key")
  second = await send_message(client, key="other-key", idempotency_key="shared-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert first.json()["id"] != second.json()["id"]
  assert await _count(session, Message) == 2
  assert await _count(session, MessageEvent) == 2
  assert await _count(session, LedgerEntry) == 2
  assert await _balance(session, tenant) == 9_997
  assert await _balance(session, other) == 9_997


async def test_402_is_not_stored_and_the_same_key_works_after_top_up(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=2)

  rejected = await send_message(client, idempotency_key="retry-key")
  assert rejected.status_code == 402
  assert await _count(session, Message) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await session.get(IdempotencyKey, (tenant.id, "retry-key")) is None

  await _top_up(session, tenant, 998)

  accepted = await send_message(client, idempotency_key="retry-key")
  assert accepted.status_code == 202
  assert await _count(session, Message) == 1
  # The top-up CREDIT and the send CHARGE.
  assert await _count(session, LedgerEntry) == 2
  assert await _balance(session, tenant) == 997
  ledger_sum = await session.scalar(
    select(func.sum(LedgerEntry.amount_cents)).where(LedgerEntry.tenant_id == tenant.id)
  )
  assert ledger_sum == 995


# BATCH MESSAGING TESTS

BATCH_PATH = "/v1/messages/batch"


def _items(count: int) -> list[dict[str, str]]:
  return [{**MESSAGE, "client_ref": f"ref-{index}"} for index in range(count)]


async def _send_batch(
  client: AsyncClient,
  items: list[dict[str, str]],
  *,
  key: str | None = API_KEY,
  idempotency_key: str | None = None,
) -> Response:
  headers = {"X-API-Key": key} if key is not None else {}
  if idempotency_key is not None:
    headers["Idempotency-Key"] = idempotency_key
  return await client.post(BATCH_PATH, json={"messages": items}, headers=headers)


async def _assert_nothing_written(
  session: AsyncSession, tenant: Tenant, balance_cents: int
) -> None:
  assert await _count(session, Message) == 0
  assert await _count(session, MessageEvent) == 0
  assert await _count(session, LedgerEntry) == 0
  assert await _balance(session, tenant) == balance_cents


async def test_batch_of_1000_is_accepted_and_charged_per_message(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000_000)

  response = await _send_batch(client, _items(1_000))

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

  assert await _count(session, Message) == 1_000
  assert await _count(session, MessageEvent) == 1_000
  assert await _count(session, LedgerEntry) == 1_000
  batch_ids = (await session.scalars(select(Message.batch_id).distinct())).all()
  assert [str(batch_id) for batch_id in batch_ids] == [body["batch_id"]]

  balance = await _balance(session, tenant)
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

  response = await _send_batch(client, _items(10), idempotency_key="broke-batch")

  assert response.status_code == 402
  assert response.json() == {"error": "INSUFFICIENT_BALANCE"}
  await _assert_nothing_written(session, tenant, 29)
  assert await session.get(IdempotencyKey, (tenant.id, "broke-batch")) is None


async def test_empty_batch_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session)

  response = await _send_batch(client, [])

  assert response.status_code == 422
  assert response.json() == {"error": "BATCH_EMPTY"}
  await _assert_nothing_written(session, tenant, 10_000)


async def test_batch_of_1001_is_422_and_writes_nothing(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session)

  response = await _send_batch(client, _items(1_001))

  assert response.status_code == 422
  assert response.json() == {"error": "BATCH_TOO_LARGE"}
  await _assert_nothing_written(session, tenant, 10_000)


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
  items = _items(5)
  items[3] = {**MESSAGE, **override}

  response = await _send_batch(client, items)

  assert response.status_code == 422
  body = response.json()
  assert body["error"] == "INVALID_BATCH_ITEM"
  (error,) = body["errors"]
  assert set(error) == {"index", "field", "code", "message"}
  assert error["index"] == 3
  assert error["field"] == field
  assert error["message"]
  await _assert_nothing_written(session, tenant, 10_000)


async def test_replay_returns_the_same_ids_and_charges_once(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000)

  first = await _send_batch(client, _items(3), idempotency_key="batch-key")
  second = await _send_batch(client, _items(3), idempotency_key="batch-key")

  assert first.status_code == 202
  assert second.status_code == 202
  assert second.json() == first.json()
  assert await _count(session, Message) == 3
  assert await _count(session, MessageEvent) == 3
  assert await _count(session, LedgerEntry) == 3
  assert await _balance(session, tenant) == 1_000 - 9


async def test_concurrent_duplicate_batches_charge_once(
  client: AsyncClient, session: AsyncSession
) -> None:
  tenant = await create_tenant(session, balance_cents=1_000)

  responses = await asyncio.gather(
    *(_send_batch(client, _items(3), idempotency_key="racing-batch") for _ in range(5))
  )

  assert {response.status_code for response in responses} == {202}
  assert len({response.json()["batch_id"] for response in responses}) == 1
  assert (
    len({tuple(message["id"] for message in response.json()["messages"]) for response in responses})
    == 1
  )
  assert await _count(session, Message) == 3
  assert await _count(session, MessageEvent) == 3
  assert await _count(session, LedgerEntry) == 3
  assert await _balance(session, tenant) == 1_000 - 9
