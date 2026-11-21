import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from redis.exceptions import RedisError
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.billing import BillingService
from app.core.dlr import (
  lock_pending_receipts,
  reconcile_dlr,
  sweep_parked_dlrs,
)
from app.db.models import (
  BillingState,
  Message,
  MessageEvent,
  MessageStatus,
  SubmitAttempt,
  SubmitOutcome,
)
from app.settings import Settings

from .limiter import CarrierRateLimiter

logger = logging.getLogger(__name__)

# Statuses a worker may claim. Anything past these is owned by the carrier or final.
CLAIMABLE = (MessageStatus.ACCEPTED, MessageStatus.QUEUED)

# How often the worker looks for parked DLRs whose carrier id has since appeared.
DLR_SWEEP_INTERVAL_SECONDS = 5.0

# A retry waits at least this fraction of its nominal delay, so jitter never hot-loops.
MIN_JITTER_RATIO = 0.5


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
    self.max_attempts = settings.max_submit_attempts
    self.backoff_base = settings.retry_backoff_base_seconds
    self.backoff_cap = settings.retry_backoff_cap_seconds

  async def claim(self) -> list[ClaimedMessage]:
    """Lease up to `claim_size` due messages, mark them QUEUED and record the event.

    One short transaction owns the whole claim: the SELECT takes a pessimistic
    row lock, the UPDATE starts the lease, and the commit drops both the lock and
    the connection. Afterwards `available_at` in the future is what keeps other
    workers off the row; a crash simply lets the lease expire. A waiting retry
    is due at `next_attempt_at`, and claiming clears it so the lease applies.
    """
    due_at = func.coalesce(Message.next_attempt_at, Message.available_at)
    due = (
      select(Message.id)
      .where(Message.status.in_(CLAIMABLE), due_at <= func.now())
      # A batch shares one now(), so id (uuid7) breaks the tie and keeps the
      # claim order stable and roughly oldest-first.
      .order_by(due_at, Message.id)
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
          .values(
            status=MessageStatus.QUEUED,
            available_at=func.now() + self.lease,
            next_attempt_at=None,
          )
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
    """Take a permit, submit once, then record the attempt and its consequence."""
    try:
      await self.limiter.acquire()
    except RedisError:
      # Fail closed: no permit, no submit. The message stays QUEUED.
      logger.warning("rate limiter unavailable; leaving %s queued", message.id)
      await self.release(message.id)
      return

    started_at = datetime.now(UTC)
    try:
      response = await self.carrier_client.post("/submit", json=message.carrier_payload())
    except httpx.TimeoutException as exc:
      # The carrier may have accepted before the response was lost. Retrying the
      # same message_id is safe because the carrier dedupes on it.
      logger.info("submit of %s timed out (%r); treating as ambiguous", message.id, exc)
      await self.record_attempt(
        message.id, SubmitOutcome.AMBIGUOUS, None, repr(exc), started_at
      )
      await self.retry_or_fail(message.id)
      return
    except httpx.HTTPError as exc:
      # A transport failure other than a timeout: the submit produced no
      # acceptance, so this attempt is definitive rather than ambiguous.
      logger.info("submit of %s failed (%r); will retry", message.id, exc)
      await self.record_attempt(
        message.id, SubmitOutcome.TRANSIENT, None, repr(exc), started_at
      )
      await self.retry_or_fail(message.id)
      return

    finished_at = datetime.now(UTC)
    if response.status_code == 200:
      carrier_id = self._carrier_id(response)
      if carrier_id is None:
        # A 200 is an acceptance, but without its carrier id the DLR cannot be
        # linked. Treat it as ambiguous and let the retry budget park it for
        # review instead of looping on the lease.
        logger.warning("submit of %s returned 200 without a carrier_id", message.id)
        await self.record_attempt(
          message.id, SubmitOutcome.AMBIGUOUS, 200, "MISSING_CARRIER_ID", started_at, finished_at
        )
        await self.retry_or_fail(message.id)
        return
      await self.record_attempt(
        message.id, SubmitOutcome.ACCEPTED, 200, None, started_at, finished_at
      )
      await self.record_submitted(message.id, carrier_id)
      return

    error = self._error_code(response)
    # Only a request the carrier definitively rejected is permanent; every other
    # status (429, 5xx, unexpected 4xx) may succeed on a later attempt.
    outcome = SubmitOutcome.PERMANENT if response.status_code == 400 else SubmitOutcome.TRANSIENT
    await self.record_attempt(
      message.id, outcome, response.status_code, error, started_at, finished_at
    )
    if outcome is SubmitOutcome.PERMANENT:
      logger.info("submit of %s was permanently rejected (%s)", message.id, error)
      await self.fail(message.id, ambiguous=False)
    else:
      logger.info("submit of %s returned %s; will retry", message.id, response.status_code)
      await self.retry_or_fail(message.id)

  @staticmethod
  def _carrier_id(response: httpx.Response) -> str | None:
    """The carrier's id from a 200 body, or None when the body is unusable."""
    try:
      payload = response.json()
    except ValueError:
      return None
    if not isinstance(payload, dict):
      return None
    carrier_id = payload.get("carrier_id")
    return carrier_id if isinstance(carrier_id, str) and carrier_id else None

  @staticmethod
  def _error_code(response: httpx.Response) -> str:
    """The carrier's machine-readable error, or a bounded body excerpt."""
    try:
      payload = response.json()
    except ValueError:
      return response.text[:200]
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
      return payload["error"]
    return response.text[:200]

  async def record_attempt(
    self,
    message_id: uuid.UUID,
    outcome: SubmitOutcome,
    http_status: int | None,
    error: str | None,
    started_at: datetime,
    finished_at: datetime | None = None,
  ) -> None:
    """Append one submit attempt in its own transaction, after the HTTP call."""
    async with self.sessions() as db:
      # Serialise attempt numbering per message: the row lock is what makes
      # max(attempt_number) + 1 unique when two workers race on one message.
      locked = await db.scalar(
        select(Message.id).where(Message.id == message_id).with_for_update()
      )
      if locked is None:
        return
      attempt_number = await db.scalar(
        select(func.coalesce(func.max(SubmitAttempt.attempt_number), 0) + 1).where(
          SubmitAttempt.message_id == message_id
        )
      )
      assert attempt_number is not None  # coalesce(..., 0) + 1 is never null
      db.add(
        SubmitAttempt(
          id=uuid.uuid7(),
          message_id=message_id,
          attempt_number=attempt_number,
          outcome=outcome.value,
          http_status=http_status,
          error=error,
          started_at=started_at,
          finished_at=finished_at or datetime.now(UTC),
        )
      )
      await db.commit()

  def backoff(self, attempt_number: int) -> timedelta:
    """Exponential delay with jitter: base, 2x, 4x, ... up to the cap."""
    seconds = min(self.backoff_cap, self.backoff_base * 2 ** (attempt_number - 1))
    return timedelta(seconds=seconds * random.uniform(MIN_JITTER_RATIO, 1.0))

  async def retry_or_fail(self, message_id: uuid.UUID) -> None:
    """Schedule the next attempt, or settle the message once attempts run out.

    Exhaustion with any ambiguous timeout leaves the charge alone and flags the
    message for review, because the carrier may have delivered and billed that
    attempt. Only definitive non-acceptance (a permanent rejection or every
    attempt failing at the transport level) refunds the charge exactly once.
    An ACCEPTED attempt is never failed: the retry budget keeps re-acquiring the
    carrier id so a lost record_submitted transaction can still settle.
    """
    async with self.sessions() as db:
      outcomes = list(
        (
          await db.execute(
            select(SubmitAttempt.outcome)
            .where(SubmitAttempt.message_id == message_id)
            .order_by(SubmitAttempt.attempt_number, SubmitAttempt.id)
          )
        )
        .scalars()
        .all()
      )
      if len(outcomes) >= self.max_attempts and SubmitOutcome.ACCEPTED not in outcomes:
        ambiguous = any(outcome == SubmitOutcome.AMBIGUOUS for outcome in outcomes)
        await self._fail(db, message_id, ambiguous=ambiguous)
      else:
        # A missing retry budget also covers an ACCEPTED attempt whose
        # record_submitted transaction was lost: keep retrying until the same
        # message_id returns its carrier id, rather than failing an accepted SMS.
        await db.execute(
          update(Message)
          .where(Message.id == message_id, Message.status == MessageStatus.QUEUED)
          .values(next_attempt_at=func.now() + self.backoff(len(outcomes)))
          .execution_options(synchronize_session=False)
        )
      await db.commit()

  async def fail(self, message_id: uuid.UUID, *, ambiguous: bool) -> bool:
    """Make one conditional, idempotent FAILED transition and refund if warranted."""
    async with self.sessions() as db:
      failed = await self._fail(db, message_id, ambiguous=ambiguous)
      await db.commit()
      return failed

  async def _fail(self, db: AsyncSession, message_id: uuid.UUID, *, ambiguous: bool) -> bool:
    """FAILED wins only before acceptance; a late DLR keeps its outcome.

    `SUBMITTED` is deliberately excluded: once a submit was accepted, a stale
    worker must not be able to fail and refund it. The DLR machine keeps its own
    wider guard for receipts.
    """
    row = (
      await db.execute(
        update(Message)
        .where(
          Message.id == message_id,
          Message.status.in_(CLAIMABLE),
          ~exists().where(
            SubmitAttempt.message_id == message_id,
            SubmitAttempt.outcome == SubmitOutcome.ACCEPTED.value,
          ),
        )
        .values(
          status=MessageStatus.FAILED,
          final_at=func.coalesce(Message.final_at, func.now()),
          billing_state=BillingState.REVIEW if ambiguous else BillingState.NORMAL,
          next_attempt_at=None,
        )
        .returning(Message.tenant_id, Message.total_price_cents)
        .execution_options(synchronize_session=False)
      )
    ).one_or_none()
    if row is None:
      return False

    await db.execute(
      pg_insert(MessageEvent)
      .values(id=uuid.uuid7(), message_id=message_id, status=MessageStatus.FAILED)
      .on_conflict_do_nothing(index_elements=["message_id", "status"])
    )
    if not ambiguous and row.total_price_cents > 0:
      # Ambiguous attempts must not refund: the carrier may have delivered.
      refunded = await BillingService(db).refund(
        row.tenant_id, message_id, row.total_price_cents
      )
      if not refunded:
        logger.warning("refund for %s was already recorded", message_id)
    return True

  async def record_submitted(self, message_id: uuid.UUID, carrier_id: str) -> None:
    """Move to SUBMITTED only from ACCEPTED/QUEUED: a status that an early DLR (or any
    other path) already moved further is kept. carrier_id and submitted_at are filled
    in if missing either way, so a late submit response still links the DLR.

    Any DLR parked for this carrier id is reconciled here, in the same transaction:
    its SUBMITTED event is recorded first, then the terminal outcome is applied. A
    terminal message keeps its status, refund and timeline; only the carrier id is
    stored as evidence.
    """
    async with self.sessions() as db:
      previous = await db.scalar(
        select(Message.status).where(Message.id == message_id).with_for_update()
      )
      if previous is None:
        return
      status = MessageStatus(previous)

      carrier = (
        await db.execute(
          update(Message)
          .where(Message.id == message_id)
          .values(
            status=MessageStatus.SUBMITTED if status in CLAIMABLE else status,
            carrier_id=func.coalesce(Message.carrier_id, carrier_id),
            submitted_at=func.coalesce(Message.submitted_at, func.now()),
            next_attempt_at=None,
          )
          .returning(Message.carrier)
          .execution_options(synchronize_session=False)
        )
      ).scalar_one_or_none()
      if carrier is None:
        return
      if status not in (*CLAIMABLE, MessageStatus.SUBMITTED):
        # NEVER append SUBMITTED after a terminal outcome: the timeline and any
        # refund stand. Parked receipts are still rejected by the DLR sweep.
        await db.commit()
        return

      pending_receipts = await lock_pending_receipts(db, carrier, carrier_id)
      await db.execute(
        pg_insert(MessageEvent)
        .values(
          id=uuid.uuid7(),
          message_id=message_id,
          status=MessageStatus.SUBMITTED,
          source="dlr" if pending_receipts else "gateway",
        )
        .on_conflict_do_nothing(index_elements=["message_id", "status"])
      )
      for inbox in pending_receipts:
        await reconcile_dlr(db, message_id, inbox)
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
