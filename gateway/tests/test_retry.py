import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing import BillingService
from app.db.database import get_sessionmaker
from app.db.models import (
  BillingState,
  DlrInbox,
  LedgerEntry,
  Message,
  MessageEvent,
  MessageStatus,
  SubmitAttempt,
  SubmitOutcome,
  Tenant,
)
from app.settings import get_settings
from app.worker.dispatch import Dispatcher
from tests.helpers import DEFAULT_KEY, create_tenant, send_message, set_message, sign_dlr

DLR_PATH = "/carrier/dlr"
CARRIER_ID = "carrier-1"


class StubLimiter:
  """Permit source that counts how often a retry re-enters the limiter."""

  def __init__(self) -> None:
    self.calls = 0

  async def acquire(self) -> None:
    self.calls += 1


def make_dispatcher(
  carrier: httpx.AsyncClient | None,
  limiter: StubLimiter | None = None,
  **settings: object,
) -> Dispatcher:
  return Dispatcher(
    sessions=get_sessionmaker(),
    carrier_client=carrier,  # type: ignore[arg-type]  # claim tests never submit
    limiter=limiter or StubLimiter(),  # type: ignore[arg-type]
    settings=get_settings().model_copy(update=settings),
  )


def carrier_client(handler) -> httpx.AsyncClient:
  return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://carrier.test")


def dlr_body(*, carrier_id: str = CARRIER_ID, status: str = "DELIVRD") -> bytes:
  return json.dumps(
    {
      "carrier_id": carrier_id,
      "status": status,
      "timestamp": datetime.now(UTC).isoformat(),
    }
  ).encode()


async def fetch(session: AsyncSession, message_id: str) -> Message:
  """Read the row as committed by the worker, bypassing this session's identity map."""
  session.expire_all()
  message = await session.get(Message, UUID(message_id))
  assert message is not None
  return message


async def attempts(session: AsyncSession, message_id: str) -> list[SubmitAttempt]:
  return list(
    (
      await session.execute(
        select(SubmitAttempt)
        .where(SubmitAttempt.message_id == UUID(message_id))
        .order_by(SubmitAttempt.attempt_number, SubmitAttempt.id)
      )
    )
    .scalars()
    .all()
  )


async def refunds(session: AsyncSession) -> list[LedgerEntry]:
  return list(
    (await session.execute(select(LedgerEntry).where(LedgerEntry.kind == "REFUND")))
    .scalars()
    .all()
  )


async def tenant_balance(session: AsyncSession) -> int:
  balance = await session.scalar(select(Tenant.balance_cents))
  assert balance is not None
  return balance


async def event_statuses(session: AsyncSession, message_id: str) -> list[str]:
  return list(
    (
      await session.execute(
        select(MessageEvent.status)
        .where(MessageEvent.message_id == UUID(message_id))
        .order_by(MessageEvent.id)
      )
    )
    .scalars()
    .all()
  )


async def test_transient_503_then_200_delivers_once_without_refund(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  submits: list[dict[str, str]] = []

  def handler(request: httpx.Request) -> httpx.Response:
    submits.append(json.loads(request.content))
    if len(submits) == 1:
      return httpx.Response(503, json={"error": "TEMPORARY_FAILURE"})
    return httpx.Response(200, json={"carrier_id": CARRIER_ID})

  limiter = StubLimiter()
  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier, limiter)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])

    waiting = await fetch(session, message_id)
    assert waiting.status == MessageStatus.QUEUED  # never SUBMITTED -> QUEUED
    assert waiting.next_attempt_at is not None
    retry_is_future = await session.scalar(
      select(Message.next_attempt_at > func.now()).where(Message.id == UUID(message_id))
    )
    assert retry_is_future is True  # the waiting retry is not hot-looped

    await dispatcher.dispatch(claimed[0])

  submitted = await fetch(session, message_id)
  assert submitted.status == MessageStatus.SUBMITTED
  assert submitted.carrier_id == CARRIER_ID
  assert submitted.next_attempt_at is None

  body = dlr_body()
  delivered = await client.post(DLR_PATH, content=body, headers=sign_dlr(body))
  assert delivered.json() == {"result": "PROCESSED"}

  final = await fetch(session, message_id)
  assert final.status == MessageStatus.DELIVERED
  assert len(submits) == 2
  assert {submit["message_id"] for submit in submits} == {message_id}
  assert limiter.calls == 2  # every attempt re-entered the limiter

  recorded = await attempts(session, message_id)
  assert [row.outcome for row in recorded] == [
    SubmitOutcome.TRANSIENT,
    SubmitOutcome.ACCEPTED,
  ]
  assert [row.http_status for row in recorded] == [503, 200]
  assert [row.attempt_number for row in recorded] == [1, 2]
  assert await refunds(session) == []
  assert await session.scalar(select(func.count()).select_from(DlrInbox)) == 1
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.SUBMITTED,
    MessageStatus.DELIVERED,
  ]


async def test_permanent_400_fails_after_one_attempt_with_one_refund(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  submits: list[dict[str, str]] = []

  def handler(request: httpx.Request) -> httpx.Response:
    submits.append(json.loads(request.content))
    return httpx.Response(400, json={"error": "INVALID_DESTINATION"})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])

  assert len(submits) == 1  # permanent failures are never retried
  failed = await fetch(session, message_id)
  assert failed.status == MessageStatus.FAILED
  assert failed.billing_state == BillingState.NORMAL
  assert failed.final_at is not None
  assert failed.next_attempt_at is None

  recorded = await attempts(session, message_id)
  assert [(row.outcome, row.http_status, row.error) for row in recorded] == [
    (SubmitOutcome.PERMANENT, 400, "INVALID_DESTINATION")
  ]

  entries = await refunds(session)
  assert len(entries) == 1
  assert entries[0].amount_cents == 3
  assert entries[0].balance_after_cents == 10_000
  assert await tenant_balance(session) == 10_000
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.FAILED,
  ]

  # A late 200 stores the carrier id as evidence but never rewrites the timeline.
  await dispatcher.record_submitted(UUID(message_id), CARRIER_ID)
  still_failed = await fetch(session, message_id)
  assert still_failed.status == MessageStatus.FAILED
  assert await event_statuses(session, message_id) == [
    MessageStatus.ACCEPTED,
    MessageStatus.QUEUED,
    MessageStatus.FAILED,
  ]

  detail = await client.get(f"/v1/messages/{message_id}", headers={"X-API-Key": DEFAULT_KEY})
  assert detail.json()["billing_state"] == "NORMAL"


async def test_exhausting_transient_failures_fails_and_refunds_once(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  submits: list[dict[str, str]] = []

  def handler(request: httpx.Request) -> httpx.Response:
    submits.append(json.loads(request.content))
    return httpx.Response(503, json={"error": "TEMPORARY_FAILURE"})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier, max_submit_attempts=3)
    claimed = await dispatcher.claim()
    for _ in range(3):
      await dispatcher.dispatch(claimed[0])

  assert len(submits) == 3  # capped, never a fourth submit
  failed = await fetch(session, message_id)
  assert failed.status == MessageStatus.FAILED
  assert failed.billing_state == BillingState.NORMAL

  recorded = await attempts(session, message_id)
  assert len(recorded) == 3
  assert {row.outcome for row in recorded} == {SubmitOutcome.TRANSIENT}

  entries = await refunds(session)
  assert len(entries) == 1
  assert entries[0].amount_cents == 3
  assert await tenant_balance(session) == 10_000


async def test_concurrent_finalisation_cannot_double_refund(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]

  def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"error": "TEMPORARY_FAILURE"})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier, max_submit_attempts=2)
    claimed = await dispatcher.claim()
    # Two workers reclaim the same message and both burn the last attempt.
    await asyncio.gather(
      dispatcher.dispatch(claimed[0]),
      dispatcher.dispatch(claimed[0]),
    )

  failed = await fetch(session, message_id)
  assert failed.status == MessageStatus.FAILED

  entries = await refunds(session)
  assert len(entries) == 1  # exactly one REFUND row...
  assert await tenant_balance(session) == 10_000  # ...and exactly one credit
  assert (
    await session.scalar(
      select(func.count())
      .select_from(MessageEvent)
      .where(
        MessageEvent.message_id == UUID(message_id),
        MessageEvent.status == MessageStatus.FAILED,
      )
    )
    == 1
  )

  # A duplicate finaliser afterwards is a no-op, not a second refund.
  assert await dispatcher.fail(UUID(message_id), ambiguous=False) is False
  assert len(await refunds(session)) == 1
  assert await tenant_balance(session) == 10_000


async def test_timeout_then_200_resubmits_the_same_message_id_and_delivers(
  client, session
) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  submits: list[dict[str, str]] = []

  def handler(request: httpx.Request) -> httpx.Response:
    submits.append(json.loads(request.content))
    if len(submits) == 1:
      raise httpx.ReadTimeout("carrier response lost", request=request)
    return httpx.Response(200, json={"carrier_id": CARRIER_ID})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])
    await dispatcher.dispatch(claimed[0])

  assert [submit["message_id"] for submit in submits] == [message_id, message_id]
  submitted = await fetch(session, message_id)
  assert submitted.status == MessageStatus.SUBMITTED
  assert submitted.carrier_id == CARRIER_ID

  body = dlr_body()
  assert (
    await client.post(DLR_PATH, content=body, headers=sign_dlr(body))
  ).json() == {"result": "PROCESSED"}

  final = await fetch(session, message_id)
  assert final.status == MessageStatus.DELIVERED
  assert [row.outcome for row in await attempts(session, message_id)] == [
    SubmitOutcome.AMBIGUOUS,
    SubmitOutcome.ACCEPTED,
  ]
  assert await refunds(session) == []
  assert await session.scalar(select(func.count()).select_from(DlrInbox)) == 1


async def test_timeout_only_exhaustion_flags_billing_review_without_refund(
  client, session
) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  submits: list[dict[str, str]] = []

  def handler(request: httpx.Request) -> httpx.Response:
    submits.append(json.loads(request.content))
    raise httpx.ReadTimeout("carrier response lost", request=request)

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier, max_submit_attempts=2)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])
    await dispatcher.dispatch(claimed[0])

  assert len(submits) == 2  # never exceeds the attempt limit
  failed = await fetch(session, message_id)
  assert failed.status == MessageStatus.FAILED
  assert failed.billing_state == BillingState.REVIEW
  assert await refunds(session) == []
  assert await tenant_balance(session) == 9_997  # the charge is held for review
  assert [row.outcome for row in await attempts(session, message_id)] == [
    SubmitOutcome.AMBIGUOUS,
    SubmitOutcome.AMBIGUOUS,
  ]

  detail = await client.get(f"/v1/messages/{message_id}", headers={"X-API-Key": DEFAULT_KEY})
  assert detail.json()["status"] == "FAILED"
  assert detail.json()["billing_state"] == "REVIEW"

  # A DLR that cannot be linked yet is parked and cannot touch the outcome.
  body = dlr_body(carrier_id="carrier-unknown")
  assert (await client.post(DLR_PATH, content=body, headers=sign_dlr(body))).json() == {
    "result": "PENDING"
  }
  parked = await fetch(session, message_id)
  assert parked.status == MessageStatus.FAILED
  assert parked.billing_state == BillingState.REVIEW

  # Once a carrier id is known, a late terminal DLR is rejected outright.
  await session.execute(
    update(Message).where(Message.id == UUID(message_id)).values(carrier_id=CARRIER_ID)
  )
  await session.commit()
  late_body = dlr_body()
  assert (
    await client.post(DLR_PATH, content=late_body, headers=sign_dlr(late_body))
  ).json() == {"result": "REJECTED"}
  reviewed = await fetch(session, message_id)
  assert reviewed.status == MessageStatus.FAILED
  assert reviewed.billing_state == BillingState.REVIEW


async def test_claim_honours_next_attempt_at_and_does_not_hot_loop(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  now = datetime.now(UTC)
  dispatcher = make_dispatcher(None)

  await session.execute(
    update(Message)
    .where(Message.id == UUID(message_id))
    .values(
      status=MessageStatus.QUEUED,
      available_at=now - timedelta(minutes=1),
      next_attempt_at=now + timedelta(minutes=5),
    )
  )
  await session.commit()

  # The retry window wins over an already-past lease: nothing is claimed.
  assert await dispatcher.claim() == []

  await session.execute(
    update(Message)
    .where(Message.id == UUID(message_id))
    .values(next_attempt_at=now - timedelta(seconds=1))
  )
  await session.commit()

  claimed = await dispatcher.claim()
  assert [str(message.id) for message in claimed] == [message_id]
  row = await fetch(session, message_id)
  assert row.status == MessageStatus.QUEUED
  assert row.next_attempt_at is None  # claiming clears the retry schedule
  assert row.available_at > now + timedelta(seconds=20)  # and takes a fresh lease


async def test_mixed_ambiguous_and_transient_exhaustion_flags_review(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  calls = 0

  def handler(request: httpx.Request) -> httpx.Response:
    nonlocal calls
    calls += 1
    if calls == 1:
      raise httpx.ReadTimeout("carrier response lost", request=request)
    return httpx.Response(503, json={"error": "TEMPORARY_FAILURE"})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier, max_submit_attempts=3)
    claimed = await dispatcher.claim()
    for _ in range(3):
      await dispatcher.dispatch(claimed[0])

  assert calls == 3
  failed = await fetch(session, message_id)
  assert failed.status == MessageStatus.FAILED
  # One lost response is enough: the carrier may have accepted and billed it.
  assert failed.billing_state == BillingState.REVIEW
  assert await refunds(session) == []
  assert await tenant_balance(session) == 9_997
  assert [row.outcome for row in await attempts(session, message_id)] == [
    SubmitOutcome.AMBIGUOUS,
    SubmitOutcome.TRANSIENT,
    SubmitOutcome.TRANSIENT,
  ]


async def test_network_error_is_transient_and_retried(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  calls = 0

  def handler(request: httpx.Request) -> httpx.Response:
    nonlocal calls
    calls += 1
    if calls == 1:
      raise httpx.ConnectError("connection refused", request=request)
    return httpx.Response(200, json={"carrier_id": CARRIER_ID})

  async with carrier_client(handler) as carrier:
    dispatcher = make_dispatcher(carrier)
    claimed = await dispatcher.claim()
    await dispatcher.dispatch(claimed[0])
    await dispatcher.dispatch(claimed[0])

  submitted = await fetch(session, message_id)
  assert submitted.status == MessageStatus.SUBMITTED
  assert [row.outcome for row in await attempts(session, message_id)] == [
    SubmitOutcome.TRANSIENT,
    SubmitOutcome.ACCEPTED,
  ]
  assert await refunds(session) == []


async def test_billing_refund_is_exactly_once(client, session) -> None:
  tenant = await create_tenant(session)
  message_id = UUID((await send_message(client)).json()["id"])
  service = BillingService(session)

  assert await service.refund(tenant.id, message_id, 3) is True
  await session.commit()
  assert await service.refund(tenant.id, message_id, 3) is False
  await session.commit()

  entries = await refunds(session)
  assert len(entries) == 1
  assert entries[0].amount_cents == 3
  assert await tenant_balance(session) == 10_000


async def test_fail_never_overwrites_an_accepted_message(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  await set_message(session, UUID(message_id), status=MessageStatus.SUBMITTED)

  dispatcher = make_dispatcher(None)
  assert await dispatcher.fail(UUID(message_id), ambiguous=False) is False

  accepted = await fetch(session, message_id)
  assert accepted.status == MessageStatus.SUBMITTED
  assert accepted.billing_state == BillingState.NORMAL
  assert await refunds(session) == []
  assert await tenant_balance(session) == 9_997


async def test_accepted_attempt_evidence_blocks_failure_and_refund(client, session) -> None:
  await create_tenant(session)
  message_id = (await send_message(client)).json()["id"]
  dispatcher = make_dispatcher(None, max_submit_attempts=1)

  # A 200 was recorded but its SUBMITTED transaction was lost; the message is
  # still QUEUED even though the carrier accepted it.
  await dispatcher.claim()
  now = datetime.now(UTC)
  await dispatcher.record_attempt(
    UUID(message_id), SubmitOutcome.ACCEPTED, 200, None, now, now
  )
  await dispatcher.retry_or_fail(UUID(message_id))

  assert await dispatcher.fail(UUID(message_id), ambiguous=False) is False
  waiting = await fetch(session, message_id)
  assert waiting.status == MessageStatus.QUEUED
  assert waiting.next_attempt_at is not None  # recover the carrier id instead
  assert await refunds(session) == []
  assert await tenant_balance(session) == 9_997
