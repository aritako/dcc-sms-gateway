import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
from redis.exceptions import RedisError
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dlr import lock_pending_dlrs, settle_dlrs, sweep_parked_dlrs
from app.db.models import DlrState, Message, MessageEvent, MessageStatus
from app.settings import Settings

from .limiter import CarrierRateLimiter

logger = logging.getLogger(__name__)

# Statuses a worker may claim. Anything past these is owned by the carrier or final.
CLAIMABLE = (MessageStatus.ACCEPTED, MessageStatus.QUEUED)

# How often the worker looks for parked DLRs whose carrier id has since appeared.
DLR_SWEEP_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
  id: uuid.UUID
  to: str
  sender_id: str
  text: str
  encoding: str

  def carrier_payload(self) -> dict[str, str]:
    """Exactly the carrier's submit contract, nothing more."""
    return {
      "message_id": str(self.id),
      "to": self.to,
      "from": self.sender_id,
      "text": self.text,
      "encoding": self.encoding,
    }


class Dispatcher:
  """Claims due messages, submits them under the global limit, records the result.

  Every database step is its own short transaction; no transaction is held open
  across an HTTP call. Crash safety comes from the lease: a claimed message
  becomes claimable again once `available_at` passes.
  """

  def __init__(
    self,
    sessions: async_sessionmaker[AsyncSession],
    carrier_client: httpx.AsyncClient,
    limiter: CarrierRateLimiter,
    settings: Settings,
  ) -> None:
    self.sessions = sessions
    self.carrier_client = carrier_client
    self.limiter = limiter
    self.lease = timedelta(seconds=settings.worker_lease_seconds)
    self.retry_delay = timedelta(seconds=settings.worker_retry_seconds)
    self.claim_size = settings.worker_claim_size

  async def claim(self) -> list[ClaimedMessage]:
    """Lease up to `claim_size` due messages, mark them QUEUED and record the event.

    One short transaction owns the whole claim: the SELECT takes a pessimistic
    row lock, the UPDATE starts the lease, and the commit drops both the lock and
    the connection. Afterwards `available_at` in the future is what keeps other
    workers off the row; a crash simply lets the lease expire.
    """
    due = (
      select(Message.id)
      .where(Message.status.in_(CLAIMABLE), Message.available_at <= func.now())
      # A batch shares one now(), so id (uuid7) breaks the tie and keeps the
      # claim order stable and roughly oldest-first.
      .order_by(Message.available_at, Message.id)
      .limit(self.claim_size)
      .with_for_update(skip_locked=True)
    )

    async with self.sessions() as db:
      ids = (await db.execute(due)).scalars().all()
      if not ids:
        return []

      # The rows are still locked, so this updates exactly the ids selected above.
      rows = (
        await db.execute(
          update(Message)
          .where(Message.id.in_(ids))
          .values(status=MessageStatus.QUEUED, available_at=func.now() + self.lease)
          .returning(
            Message.id, Message.to_number, Message.sender_id, Message.text, Message.encoding
          )
          .execution_options(synchronize_session=False)
        )
      ).all()
      # A reclaimed message already has its QUEUED event; DO NOTHING keeps the first.
      events = [
        {"id": uuid.uuid7(), "message_id": row.id, "status": MessageStatus.QUEUED} for row in rows
      ]
      await db.execute(
        pg_insert(MessageEvent)
        .values(events)
        .on_conflict_do_nothing(index_elements=["message_id", "status"])
      )
      await db.commit()
    return [ClaimedMessage(*row) for row in rows]

  async def dispatch(self, message: ClaimedMessage) -> None:
    """Take a permit, submit once, then record SUBMITTED or release for a later retry."""
    try:
      await self.limiter.acquire()
    except RedisError:
      # Fail closed: no permit, no submit. The message stays QUEUED.
      logger.warning("rate limiter unavailable; leaving %s queued", message.id)
      await self.release(message.id)
      return

    try:
      response = await self.carrier_client.post("/submit", json=message.carrier_payload())
    except httpx.HTTPError as error:
      # Includes the 2s timeout. The carrier may still have accepted the message;
      # resubmitting the same message_id is safe because the carrier dedupes on it.
      logger.info("submit of %s failed (%r); will retry", message.id, error)
      await self.release(message.id)
      return

    if response.status_code != 200:
      # Ticket 13 adds the real policy (400 is permanent, max attempts, refunds).
      logger.info("submit of %s returned %s; will retry", message.id, response.status_code)
      await self.release(message.id)
      return

    await self.record_submitted(message.id, response.json()["carrier_id"])

  async def record_submitted(self, message_id: uuid.UUID, carrier_id: str) -> None:
    """Move to SUBMITTED only from ACCEPTED/QUEUED: a status that an early DLR (or any
    other path) already moved further is kept. carrier_id and submitted_at are filled
    in if missing either way, so a late submit response still links the DLR.

    Any DLR parked for this carrier id is reconciled here, in the same transaction:
    its SUBMITTED event is recorded first, then the terminal outcome is applied.
    """
    async with self.sessions() as db:
      carrier = (
        await db.execute(
          update(Message)
          .where(Message.id == message_id)
          .values(
            status=case(
              (Message.status.in_(CLAIMABLE), MessageStatus.SUBMITTED), else_=Message.status
            ),
            carrier_id=func.coalesce(Message.carrier_id, carrier_id),
            submitted_at=func.coalesce(Message.submitted_at, func.now()),
          )
          .returning(Message.carrier)
          .execution_options(synchronize_session=False)
        )
      ).scalar_one_or_none()
      if carrier is None:
        return

      pending = await lock_pending_dlrs(db, carrier=carrier, carrier_id=carrier_id)
      await db.execute(
        pg_insert(MessageEvent)
        .values(
          id=uuid.uuid7(),
          message_id=message_id,
          status=MessageStatus.SUBMITTED,
          source="dlr" if pending else "gateway",
        )
        .on_conflict_do_nothing(index_elements=["message_id", "status"])
      )
      await settle_dlrs(db, message_id, pending, state=DlrState.RECONCILED)
      await db.commit()

  async def reconcile_parked_dlrs(self) -> int:
    """One sweep for early DLRs that lost the race with the submit response."""
    async with self.sessions() as db:
      reconciled = await sweep_parked_dlrs(db)
      await db.commit()
    return reconciled

  async def release(self, message_id: uuid.UUID) -> None:
    """Hand the message back after a delay, with no status change (no hot loop)."""
    async with self.sessions() as db:
      await db.execute(
        update(Message)
        .where(Message.id == message_id, Message.status == MessageStatus.QUEUED)
        .values(available_at=func.now() + self.retry_delay)
        .execution_options(synchronize_session=False)
      )
      await db.commit()

  async def run_once(self) -> int:
    """One round: claim a batch and dispatch it concurrently. Returns the batch size."""
    claimed = await self.claim()
    results = await asyncio.gather(
      *(self.dispatch(message) for message in claimed), return_exceptions=True
    )
    for message, result in zip(claimed, results, strict=True):
      if isinstance(result, Exception):
        # Its lease expires and another round picks it up; nothing is lost.
        logger.error("dispatch of %s crashed", message.id, exc_info=result)
    return len(claimed)

  async def run(self, idle_seconds: float) -> None:
    loop = asyncio.get_running_loop()
    last_sweep = 0.0
    while True:
      try:
        if loop.time() - last_sweep >= DLR_SWEEP_INTERVAL_SECONDS:
          last_sweep = loop.time()
          try:
            await self.reconcile_parked_dlrs()
          except Exception:
            # A sweep failure must not stop submissions; the next one retries.
            logger.exception("DLR sweep failed")
        if await self.run_once() == 0:
          await asyncio.sleep(idle_seconds)
      except Exception:
        logger.exception("dispatch round failed; backing off")
        await asyncio.sleep(self.retry_delay.total_seconds())
