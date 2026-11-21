import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DlrInbox, DlrState, Message, MessageEvent, MessageStatus

# The carrier's wire codes, mapped to the gateway's terminal statuses.
CARRIER_STATUS_MAP: dict[str, MessageStatus] = {
  "DELIVRD": MessageStatus.DELIVERED,
  "UNDELIV": MessageStatus.UNDELIVERED,
  "EXPIRED": MessageStatus.EXPIRED,
}

# A terminal DLR may follow any non-terminal state, but never FAILED or another
# terminal: the first terminal outcome wins and the timeline never regresses.
NONTERMINAL_STATUS = (
  MessageStatus.ACCEPTED,
  MessageStatus.QUEUED,
  MessageStatus.SUBMITTED,
)

ALREADY_TERMINAL = "MESSAGE_ALREADY_TERMINAL"


async def apply_terminal_dlr(
  db: AsyncSession,
  message_id: uuid.UUID,
  *,
  status: MessageStatus,
  occurred_at: datetime,
  received_at: datetime,
) -> bool:
  """Apply one terminal status with a forward-only conditional UPDATE.

  Returns False when the message is already terminal (or FAILED), in which case
  nothing is written. The caller owns the transaction.
  """
  applied = (
    await db.execute(
      update(Message)
      .where(Message.id == message_id, Message.status.in_(NONTERMINAL_STATUS))
      .values(
        status=status,
        final_at=func.coalesce(Message.final_at, func.now()),
      )
      .returning(Message.id)
      .execution_options(synchronize_session=False)
    )
  ).scalar_one_or_none()
  if applied is None:
    return False

  # A duplicate of the same terminal status is a no-op rather than an error.
  await db.execute(
    pg_insert(MessageEvent)
    .values(
      id=uuid.uuid7(),
      message_id=message_id,
      status=status,
      source="dlr",
      occurred_at=occurred_at,
      received_at=received_at,
    )
    .on_conflict_do_nothing(index_elements=["message_id", "status"])
  )
  return True


async def lock_pending_dlrs(
  db: AsyncSession, *, carrier: str, carrier_id: str
) -> Sequence[DlrInbox]:
  """Parked receipts for this carrier id, locked so only one reconciler applies them."""
  rows = (
    await db.execute(
      select(DlrInbox)
      .where(
        DlrInbox.carrier == carrier,
        DlrInbox.carrier_id == carrier_id,
        DlrInbox.state == DlrState.PENDING.value,
      )
      .order_by(DlrInbox.occurred_at, DlrInbox.received_at, DlrInbox.id)
      .with_for_update(skip_locked=True)
    )
  ).scalars()
  return rows.all()


async def settle_dlrs(
  db: AsyncSession,
  message_id: uuid.UUID,
  rows: Sequence[DlrInbox],
  *,
  state: DlrState,
) -> int:
  """Apply parked receipts to `message_id` and record the outcome on each row."""
  applied_count = 0
  for inbox in rows:
    applied = await apply_terminal_dlr(
      db,
      message_id,
      status=MessageStatus(inbox.status),
      occurred_at=inbox.occurred_at,
      received_at=inbox.received_at,
    )
    inbox.state = (state if applied else DlrState.REJECTED).value
    inbox.rejection_reason = None if applied else ALREADY_TERMINAL
    applied_count += int(applied)
  return applied_count


async def ingest_dlr(
  db: AsyncSession,
  *,
  carrier: str,
  carrier_id: str,
  status: MessageStatus,
  occurred_at: datetime,
) -> str:
  """Record a receipt exactly once and apply it when its message is known.

  Returns APPLIED, PARKED, DUPLICATE or REJECTED. The caller commits.
  """
  inbox = (
    await db.execute(
      pg_insert(DlrInbox)
      .values(
        carrier=carrier,
        carrier_id=carrier_id,
        status=status.value,
        occurred_at=occurred_at,
      )
      .on_conflict_do_nothing(
        index_elements=["carrier", "carrier_id", "status", "occurred_at"]
      )
      .returning(DlrInbox.id, DlrInbox.received_at)
    )
  ).one_or_none()
  if inbox is None:
    return "DUPLICATE"

  message_id = await db.scalar(
    select(Message.id).where(
      Message.carrier == carrier,
      Message.carrier_id == carrier_id,
    )
  )
  if message_id is None:
    # The submit response has not stored the carrier id yet; keep it parked.
    return "PARKED"

  applied = await apply_terminal_dlr(
    db,
    message_id,
    status=status,
    occurred_at=occurred_at,
    received_at=inbox.received_at,
  )
  await db.execute(
    update(DlrInbox)
    .where(DlrInbox.id == inbox.id)
    .values(
      state=(DlrState.PROCESSED if applied else DlrState.REJECTED).value,
      rejection_reason=None if applied else ALREADY_TERMINAL,
    )
    .execution_options(synchronize_session=False)
  )
  return "APPLIED" if applied else "REJECTED"


async def sweep_parked_dlrs(db: AsyncSession, *, limit: int = 50) -> int:
  """Reconcile parked receipts whose carrier id has appeared since they arrived.

  This closes the insert-after-query race between a DLR and the submit response.
  The caller owns the transaction.
  """
  rows = (
    await db.execute(
      select(DlrInbox, Message.id)
      .join(
        Message,
        and_(
          Message.carrier == DlrInbox.carrier,
          Message.carrier_id == DlrInbox.carrier_id,
        ),
      )
      .where(DlrInbox.state == DlrState.PENDING.value)
      .order_by(DlrInbox.received_at, DlrInbox.id)
      .limit(limit)
      .with_for_update(of=DlrInbox, skip_locked=True)
    )
  ).all()

  for inbox, message_id in rows:
    applied = await apply_terminal_dlr(
      db,
      message_id,
      status=MessageStatus(inbox.status),
      occurred_at=inbox.occurred_at,
      received_at=inbox.received_at,
    )
    inbox.state = (DlrState.RECONCILED if applied else DlrState.REJECTED).value
    inbox.rejection_reason = None if applied else ALREADY_TERMINAL
  return len(rows)
