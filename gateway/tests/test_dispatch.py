import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from helpers import (
  StubLimiter,
  create_tenant,
  event_statuses,
  make_dispatcher,
  send_batch,
  send_message,
  set_message,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, MessageStatus


async def message_row(session: AsyncSession, message_id) -> object:
  """Status columns plus the lease and retry clocks, measured on the database clock."""
  return (
    await session.execute(
      select(
        Message.status,
        Message.carrier_id,
        Message.submitted_at,
        func.extract("epoch", Message.available_at - func.now()).label("due_in"),
        Message.next_attempt_at,
      ).where(Message.id == message_id)
    )
  ).one()


async def test_claim_leases_due_messages_and_marks_them_queued(client, session) -> None:
  await create_tenant(session)
  sent = await send_batch(client, 3)
  ids = {message["id"] for message in sent.json()["messages"]}

  claimed = await make_dispatcher(None).claim()

  assert {str(message.id) for message in claimed} == ids
  for message_id in ids:
    row = await message_row(session, message_id)
    assert row.status == MessageStatus.QUEUED
    assert row.due_in > 20  # leased for worker_lease_seconds (30), not due again
    assert row.carrier_id is None and row.submitted_at is None
    # ACCEPTED from the send, QUEUED from the claim, and no duplicate on re-claim.
    assert await event_statuses(session, message_id) == [
      MessageStatus.ACCEPTED,
      MessageStatus.QUEUED,
    ]


async def test_claim_takes_released_queued_but_not_leased_or_finished(client, session) -> None:
  await create_tenant(session)
  released = (await send_message(client)).json()["id"]
  leased = (await send_message(client)).json()["id"]
  finished = (await send_message(client)).json()["id"]

  # A failed send: still QUEUED, put back on the queue by `release`.
  await set_message(
    session,
    released,
    status=MessageStatus.QUEUED,
    available_at=datetime.now(UTC) - timedelta(seconds=1),
  )
  # Another worker still owns this one.
  await set_message(
    session,
    leased,
    status=MessageStatus.QUEUED,
    available_at=datetime.now(UTC) + timedelta(minutes=5),
  )
  # Already past the queue.
  await set_message(session, finished, status=MessageStatus.SUBMITTED)

  claimed = await make_dispatcher(None).claim()

  assert [str(message.id) for message in claimed] == [released]
  # set_message recorded its QUEUED event; the claim's insert must not add another.
  assert await event_statuses(session, released) == [MessageStatus.ACCEPTED, MessageStatus.QUEUED]
  assert (await message_row(session, leased)).due_in > 250
  assert (await message_row(session, finished)).status == MessageStatus.SUBMITTED


async def test_claim_serves_a_short_tenant_before_the_backlog(client, session) -> None:
  await create_tenant(session, name="Campaign", api_key="campaign-key")
  await create_tenant(session, name="Bank", api_key="bank-key")
  campaign = [
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 5, key="campaign-key")).json()["messages"]
  ]
  bank = [
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 1, key="bank-key")).json()["messages"]
  ]

  dispatcher = make_dispatcher(None, worker_claim_size=4)
  first = [message.id for message in await dispatcher.claim()]
  second = [message.id for message in await dispatcher.claim()]

  # The bank's single message is claimed with the campaign's first share, not
  # after the campaign's whole backlog, and the campaign keeps FIFO within itself.
  assert len(first) == 4
  assert bank[0] in first
  assert len(set(first) & set(campaign)) == 3
  assert max(set(first) & set(campaign)) < min(set(second) & set(campaign))
  assert set(second) == set(campaign) - set(first)


async def test_claim_splits_equal_backlogs_evenly(client, session) -> None:
  await create_tenant(session, name="Campaign", api_key="campaign-key")
  await create_tenant(session, name="Bank", api_key="bank-key")
  campaign = {
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 10, key="campaign-key")).json()["messages"]
  }
  bank = {
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 10, key="bank-key")).json()["messages"]
  }

  claimed = [message.id for message in await make_dispatcher(None, worker_claim_size=4).claim()]

  assert len(set(claimed) & campaign) == 2
  assert len(set(claimed) & bank) == 2


async def test_claim_spreads_an_uneven_batch_across_tenants(client, session) -> None:
  keys = ["a-key", "b-key", "c-key"]
  for index, key in enumerate(keys):
    await create_tenant(session, name=f"Tenant {index}", api_key=key)
  tenants = {
    key: {
      uuid.UUID(message["id"])
      for message in (await send_batch(client, 5, key=key)).json()["messages"]
    }
    for key in keys
  }

  claimed = [message.id for message in await make_dispatcher(None, worker_claim_size=4).claim()]
  shares = sorted(len(set(claimed) & ids) for ids in tenants.values())

  # 4 slots over 3 tenants: the earliest head (a-key) gets the extra slot.
  assert shares == [1, 1, 2]
  assert len(set(claimed) & tenants["a-key"]) == 2


async def test_claim_gives_one_turn_when_tenants_outnumber_slots(client, session) -> None:
  keys = ["a-key", "b-key", "c-key", "d-key", "e-key"]
  for index, key in enumerate(keys):
    await create_tenant(session, name=f"Tenant {index}", api_key=key)
  tenants = {}
  for key in keys:
    response = await send_batch(client, 1, key=key)
    tenants[key] = {uuid.UUID(message["id"]) for message in response.json()["messages"]}

  claimed = [message.id for message in await make_dispatcher(None, worker_claim_size=4).claim()]

  # Five tenants, four slots: four different tenants get one message each.
  assert len(claimed) == 4
  assert len(set(claimed)) == 4
  assert sum(bool(set(claimed) & ids) for ids in tenants.values()) == 4


async def test_three_workers_claim_disjoint_and_fair_batches(client, session) -> None:
  await create_tenant(session, name="Campaign", api_key="campaign-key")
  await create_tenant(session, name="Bank", api_key="bank-key")
  campaign = {
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 10, key="campaign-key")).json()["messages"]
  }
  bank = {
    uuid.UUID(message["id"])
    for message in (await send_batch(client, 10, key="bank-key")).json()["messages"]
  }

  workers = [make_dispatcher(None, worker_claim_size=4) for _ in range(3)]
  batches = await asyncio.gather(*(worker.claim() for worker in workers))
  claimed = [message.id for batch in batches for message in batch]

  # SKIP LOCKED hands each row to exactly one worker, and every worker gets its
  # two-message share per tenant, so the first three rounds are even.
  assert len(claimed) == 12
  assert len(claimed) == len(set(claimed))
  assert len(set(claimed) & campaign) == 6
  assert len(set(claimed) & bank) == 6


async def test_claim_orders_a_due_retry_before_a_newer_first_attempt(client, session) -> None:
  await create_tenant(session)
  retry = (await send_message(client)).json()["id"]
  fresh = (await send_message(client)).json()["id"]
  await session.execute(
    update(Message)
    .where(Message.id == retry)
    .values(
      status=MessageStatus.QUEUED,
      available_at=datetime.now(UTC) + timedelta(minutes=5),
      next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
  )
  await session.commit()

  dispatcher = make_dispatcher(None, worker_claim_size=1)
  first = await dispatcher.claim()
  second = await dispatcher.claim()

  # The retry is due at next_attempt_at even though its old lease (available_at)
  # is still in the future, so it goes before the newer first attempt.
  assert [str(message.id) for message in first] == [retry]
  assert [str(message.id) for message in second] == [fresh]


async def test_dispatch_records_submitted_after_a_200(
  client, session, fake_carrier, fake_carrier_client
) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  dispatcher = make_dispatcher(fake_carrier_client)

  claimed = await dispatcher.claim()
  await dispatcher.dispatch(claimed[0])

  row = await message_row(session, message_id)
  assert row.status == MessageStatus.SUBMITTED
  assert row.carrier_id == "fake-1"
  assert row.submitted_at is not None
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.SUBMITTED,
  ]
  # Exactly the carrier's contract, with the message id the carrier dedupes on.
  assert fake_carrier.submits == [
    {
      "message_id": str(message_id),
      "to": "+8801712345678",
      "from": "ACME",
      "text": "hello",
      "encoding": "GSM7",
    }
  ]


async def test_dispatch_schedules_a_retry_on_a_non_200(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  limiter = StubLimiter()
  transport = httpx.MockTransport(
    lambda request: httpx.Response(503, json={"error": "TEMPORARY_FAILURE"})
  )

  async with httpx.AsyncClient(transport=transport, base_url="http://carrier.test") as carrier:
    dispatcher = make_dispatcher(carrier, limiter)
    claimed = await dispatcher.claim()
    retry_requested_at = datetime.now(UTC)
    await dispatcher.dispatch(claimed[0])

  row = await message_row(session, message_id)
  assert row.status == MessageStatus.QUEUED  # not terminal
  assert row.carrier_id is None and row.submitted_at is None
  # Retried after a jittered backoff of at most 1 s, so no hot loop.
  assert row.next_attempt_at is not None
  assert row.next_attempt_at > retry_requested_at
  assert row.due_in > 20  # still leased while the retry waits
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
  ]
  assert limiter.calls == 1


async def test_dispatch_fails_closed_when_the_limiter_is_down(
  client, session, fake_carrier, fake_carrier_client
) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  limiter = StubLimiter(down=True)
  dispatcher = make_dispatcher(fake_carrier_client, limiter)

  claimed = await dispatcher.claim()
  await dispatcher.dispatch(claimed[0])

  assert limiter.calls == 1
  assert fake_carrier.submits == []  # never reached the carrier
  row = await message_row(session, message_id)
  assert row.status == MessageStatus.QUEUED
  assert row.carrier_id is None
  assert 4 < row.due_in <= 6  # released back to the queue
