import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_sessionmaker
from app.db.models import (
  DlrInbox,
  DlrState,
  Message,
  MessageEvent,
  MessageStatus,
)
from app.settings import get_settings
from app.worker.dispatch import Dispatcher
from tests.helpers import create_tenant, send_message, sign_dlr

DLR_PATH = "/carrier/dlr"
CARRIER_ID = "carrier-1"
OCCURRED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


def dlr_body(
  *,
  carrier_id: str = CARRIER_ID,
  status: str = "DELIVRD",
  timestamp: datetime | None = None,
) -> bytes:
  return json.dumps(
    {
      "carrier_id": carrier_id,
      "status": status,
      "timestamp": (timestamp or OCCURRED_AT).isoformat(),
    }
  ).encode()


async def post_dlr(client, body: bytes):
  return await client.post(DLR_PATH, content=body, headers=sign_dlr(body))


class AllowAllLimiter:
  async def acquire(self) -> None:
    return None


def make_dispatcher(carrier: httpx.AsyncClient | None) -> Dispatcher:
  return Dispatcher(
    sessions=get_sessionmaker(),
    carrier_client=carrier,  # type: ignore[arg-type]  # sweeps never submit
    limiter=AllowAllLimiter(),  # type: ignore[arg-type]
    settings=get_settings(),
  )


def carrier_client(carrier_id: str = CARRIER_ID) -> httpx.AsyncClient:
  transport = httpx.MockTransport(
    lambda request: httpx.Response(200, json={"carrier_id": carrier_id})
  )
  return httpx.AsyncClient(transport=transport, base_url="http://carrier.test")


async def mark_submitted(
  session: AsyncSession, message_id: UUID, carrier_id: str = CARRIER_ID
) -> None:
  """Put a message in the state a submit 200 would leave behind."""
  await session.execute(
    update(Message)
    .where(Message.id == message_id)
    .values(
      status=MessageStatus.SUBMITTED,
      carrier_id=carrier_id,
      submitted_at=func.now(),
    )
  )
  session.add(MessageEvent(message_id=message_id, status=MessageStatus.SUBMITTED))
  await session.commit()


async def row_counts(session: AsyncSession) -> tuple[int, int, int]:
  return (
    await session.scalar(select(func.count()).select_from(Message)),
    await session.scalar(select(func.count()).select_from(MessageEvent)),
    await session.scalar(select(func.count()).select_from(DlrInbox)),
  )


async def event_statuses(session: AsyncSession, message_id: UUID) -> list[str]:
  return list(
    (
      await session.execute(
        select(MessageEvent.status)
        .where(MessageEvent.message_id == message_id)
        .order_by(MessageEvent.id)
      )
    )
    .scalars()
    .all()
  )


async def test_missing_or_wrong_signature_is_401_with_no_writes(client, session) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  await mark_submitted(session, message_id)
  before = await row_counts(session)
  body = dlr_body()

  missing = await client.post(DLR_PATH, content=body)
  wrong = await client.post(DLR_PATH, content=body, headers={"X-Signature": "0" * 64})
  wrong_secret = await client.post(
    DLR_PATH, content=body, headers=sign_dlr(body, secret="not-the-secret")
  )

  assert [missing.status_code, wrong.status_code, wrong_secret.status_code] == [401, 401, 401]
  assert await row_counts(session) == before
  message = await session.get(Message, message_id)
  assert message is not None and message.status == MessageStatus.SUBMITTED


async def test_delivrd_marks_the_message_delivered_with_both_timestamps(client, session) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  await mark_submitted(session, message_id)

  response = await post_dlr(client, dlr_body())

  assert response.status_code == 200
  assert response.json() == {"result": "APPLIED"}
  message = await session.get(Message, message_id)
  assert message is not None
  assert message.status == MessageStatus.DELIVERED
  assert message.final_at is not None

  event = (
    await session.execute(
      select(MessageEvent).where(
        MessageEvent.message_id == message_id,
        MessageEvent.status == MessageStatus.DELIVERED,
      )
    )
  ).scalar_one()
  assert event.source == "dlr"
  assert event.occurred_at == OCCURRED_AT
  assert event.received_at is not None

  inbox = (await session.execute(select(DlrInbox))).scalar_one()
  assert inbox.state == DlrState.PROCESSED


async def test_a_duplicate_dlr_writes_no_second_event_and_keeps_final_at(client, session) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  await mark_submitted(session, message_id)
  body = dlr_body()

  assert (await post_dlr(client, body)).status_code == 200
  message = await session.get(Message, message_id)
  assert message is not None
  first_final_at = message.final_at

  duplicate = await post_dlr(client, body)

  assert duplicate.status_code == 200
  assert duplicate.json() == {"result": "DUPLICATE"}
  await session.refresh(message)
  assert message.final_at == first_final_at
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.SUBMITTED,
    MessageStatus.DELIVERED,
  ]
  assert await session.scalar(select(func.count()).select_from(DlrInbox)) == 1
  assert await session.scalar(select(func.count()).select_from(MessageEvent).where(
    MessageEvent.message_id == message_id,
    MessageEvent.status == MessageStatus.DELIVERED,
  )) == 1


async def test_early_dlr_is_parked_then_reconciled_by_the_submit_response(
  client, session
) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])

  parked = await post_dlr(client, dlr_body())
  assert parked.status_code == 200
  assert parked.json() == {"result": "PARKED"}
  message = await session.get(Message, message_id)
  assert message is not None
  assert message.status == MessageStatus.ACCEPTED
  assert message.carrier_id is None
  assert (await session.execute(select(DlrInbox))).scalar_one().state == DlrState.PENDING

  async with carrier_client() as carrier:
    dispatcher = make_dispatcher(carrier)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])

  await session.refresh(message)
  assert message.status == MessageStatus.DELIVERED
  assert message.carrier_id == CARRIER_ID
  assert message.final_at is not None
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.SUBMITTED,
    MessageStatus.DELIVERED,
  ]
  sources = (
    (
      await session.execute(
        select(MessageEvent.source)
        .where(MessageEvent.message_id == message_id)
        .order_by(MessageEvent.id)
      )
    )
    .scalars()
    .all()
  )
  assert list(sources[-2:]) == ["dlr", "dlr"]
  assert (await session.execute(select(DlrInbox))).scalar_one().state == DlrState.RECONCILED

  # A late submit 200 keeps the terminal outcome and the carrier id it already has.
  await dispatcher.record_submitted(message_id, CARRIER_ID)
  await session.refresh(message)
  assert message.status == MessageStatus.DELIVERED
  assert message.carrier_id == CARRIER_ID
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.SUBMITTED,
    MessageStatus.DELIVERED,
  ]
  assert await session.scalar(
    select(func.count()).select_from(Message).where(Message.carrier_id == CARRIER_ID)
  ) == 1


async def test_a_late_conflicting_terminal_is_rejected_and_does_not_regress(
  client, session
) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  await mark_submitted(session, message_id)

  assert (await post_dlr(client, dlr_body())).status_code == 200
  message = await session.get(Message, message_id)
  assert message is not None
  delivered_at = message.final_at
  events_before = await event_statuses(session, message_id)

  late = await post_dlr(
    client, dlr_body(status="UNDELIV", timestamp=OCCURRED_AT + timedelta(seconds=30))
  )

  assert late.status_code == 200
  assert late.json() == {"result": "REJECTED"}
  await session.refresh(message)
  assert message.status == MessageStatus.DELIVERED
  assert message.final_at == delivered_at
  assert await event_statuses(session, message_id) == events_before

  inboxes = (
    (await session.execute(select(DlrInbox).order_by(DlrInbox.received_at)))
    .scalars()
    .all()
  )
  assert [inbox.state for inbox in inboxes] == [DlrState.PROCESSED, DlrState.REJECTED]
  assert inboxes[1].rejection_reason is not None


async def test_sweep_reconciles_a_dlr_parked_before_the_carrier_id_was_saved(
  client, session
) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  assert (await post_dlr(client, dlr_body())).json() == {"result": "PARKED"}

  # The worker saved the carrier id, but this DLR arrived just after its query.
  await session.execute(
    update(Message)
    .where(Message.id == message_id)
    .values(
      status=MessageStatus.SUBMITTED,
      carrier_id=CARRIER_ID,
      submitted_at=func.now(),
    )
  )
  session.add(MessageEvent(message_id=message_id, status=MessageStatus.SUBMITTED))
  await session.commit()

  reconciled = await make_dispatcher(None).reconcile_parked_dlrs()

  assert reconciled == 1
  message = await session.get(Message, message_id)
  assert message is not None
  assert message.status == MessageStatus.DELIVERED
  assert (await session.execute(select(DlrInbox))).scalar_one().state == DlrState.RECONCILED
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.SUBMITTED,
    MessageStatus.DELIVERED,
  ]


async def test_invalid_status_or_body_is_400_with_no_writes(client, session) -> None:
  await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  await mark_submitted(session, message_id)
  before = await row_counts(session)

  unknown_status = dlr_body(status="MAYBE")
  broken_json = b'{"carrier_id": "carrier-1", "status": '

  for body in (unknown_status, broken_json):
    response = await client.post(DLR_PATH, content=body, headers=sign_dlr(body))
    assert response.status_code == 400
    assert response.json() == {"error": "INVALID_DLR"}

  assert await row_counts(session) == before
