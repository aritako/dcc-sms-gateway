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
from sqlalchemy import func, select
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
        func.extract("epoch", Message.next_attempt_at - func.now()).label("retry_in"),
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
    await dispatcher.dispatch(claimed[0])

  row = await message_row(session, message_id)
  assert row.status == MessageStatus.QUEUED  # not terminal
  assert row.carrier_id is None and row.submitted_at is None
  # Retried after a jittered backoff of at most 1 s, so no hot loop.
  assert 0 < row.retry_in <= 1.5
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
