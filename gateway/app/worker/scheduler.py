"""Tenant-fair claim selection for the dispatch workers.

The `messages` table is the queue, so scheduling is the query that decides which
due rows a worker leases next. Keeping that decision here leaves `Dispatcher`
responsible for the lease, the carrier call and the status transitions.
"""

from sqlalchemy import Integer, case, cast, func, select, true
from sqlalchemy.sql import Executable

from app.db.models import Message, MessageStatus

# Statuses a worker may claim. Anything past these is owned by the carrier or final.
CLAIMABLE = (MessageStatus.ACCEPTED, MessageStatus.QUEUED)


def claim_query(claim_size: int) -> Executable:
  """Build the query that selects the next tenant-fair batch of due messages.

  A claim is one round. Active tenants are ranked by their oldest due message,
  and the batch is split into equal shares: every tenant gets `claim_size // N`
  slots and the first `claim_size % N` tenants get one more. When tenants
  outnumber the slots, only the first `claim_size` heads get one each. A tenant
  with less work than its share leaves the unused slots to `fill`, the globally
  oldest due messages, so one busy tenant can still use the whole batch.

  Within a tenant both parts order by due time, so first attempts stay FIFO and
  retries enter at `next_attempt_at`; a retry that is not due yet is invisible.
  The shares are per claim, not persistent deficits. Locks are taken below each
  tenant's LIMIT, so losing a row to another worker does not cost the tenant its
  turn. When tenants outnumber the slots, the first `claim_size` by oldest head
  get one each; a tenant with a large old backlog can stay in that prefix until
  it drains, which is why a persisted last-served cursor is the post-MVP fix.
  """
  due_at = func.coalesce(Message.next_attempt_at, Message.available_at)
  active = (
    select(Message.tenant_id, func.min(due_at).label("head_due"))
    .where(Message.status.in_(CLAIMABLE), due_at <= func.now())
    .group_by(Message.tenant_id)
    .cte("active")
  )
  ranked = (
    select(
      active.c.tenant_id,
      active.c.head_due,
      func.row_number()
      .over(order_by=(active.c.head_due, active.c.tenant_id))
      .label("turn"),
      func.count().over().label("tenants"),
    )
    .cte("ranked")
  )
  # q = K // N, r = K % N: the first r tenants in head-due order get q + 1.
  quota = cast(func.floor(claim_size / ranked.c.tenants), Integer) + case(
    (ranked.c.turn <= claim_size % ranked.c.tenants, 1), else_=0
  )
  selected = (
    select(ranked.c.tenant_id, quota.label("quota"))
    .where(ranked.c.turn <= claim_size)
    .cte("selected")
  )
  head = (
    select(Message.id)
    .where(
      Message.tenant_id == selected.c.tenant_id,
      Message.status.in_(CLAIMABLE),
      due_at <= func.now(),
    )
    .order_by(due_at, Message.id)
    .limit(selected.c.quota)
    .with_for_update(skip_locked=True)
    .lateral("head")
  )
  claimed = select(head.c.id).select_from(selected.join(head, true())).cte("claimed")
  remaining = claim_size - select(func.count()).select_from(claimed).scalar_subquery()
  fill = (
    select(Message.id)
    .where(
      Message.status.in_(CLAIMABLE),
      due_at <= func.now(),
      Message.id.not_in(select(claimed.c.id)),
    )
    .order_by(due_at, Message.id)
    .limit(remaining)
    .with_for_update(skip_locked=True)
    .cte("fill")
  )
  return select(claimed.c.id).union_all(select(fill.c.id))
