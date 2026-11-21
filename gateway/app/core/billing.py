import uuid

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LedgerEntry, LedgerKind, Tenant


class InsufficientBalance(Exception):
  """The tenant's balance cannot cover the charge."""


class BillingService:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def charge(self, tenant_id: uuid.UUID, message_id: uuid.UUID, amount_cents: int) -> int:
    return await self.charge_many(tenant_id=tenant_id, charges=[(message_id, amount_cents)])

  async def charge_many(self, tenant_id: uuid.UUID, charges: list[tuple[uuid.UUID, int]]) -> int:
    """Debit every charge in one aggregate update and write one CHARGE row each.

    Messages must already be inserted: the ledger rows reference them.
    The caller commits.
    """
    if not charges or any(amount <= 0 for _, amount in charges):
      raise ValueError("charges must be non-empty and every amount positive")
    total = sum(amount for _, amount in charges)

    balance_after = await self.db.scalar(
      update(Tenant)
      .where(Tenant.id == tenant_id, Tenant.balance_cents >= total)
      .values(balance_cents=Tenant.balance_cents - total)
      .returning(Tenant.balance_cents)
      # Plain SQL statement; don't let the ORM touch in-memory Tenant objects.
      .execution_options(synchronize_session=False)
    )
    if balance_after is None:
      raise InsufficientBalance(tenant_id)

    running = balance_after + total
    entries = []
    for message_id, amount in charges:
      running -= amount
      entries.append(
        {
          "id": uuid.uuid7(),
          "tenant_id": tenant_id,
          "message_id": message_id,
          "kind": LedgerKind.CHARGE,
          "amount_cents": -amount,
          "balance_after_cents": running,
        }
      )
    await self.db.execute(insert(LedgerEntry), entries)
    return balance_after

  async def refund(
    self, tenant_id: uuid.UUID, message_id: uuid.UUID, amount_cents: int
  ) -> bool:
    """Credit one message's charge back exactly once.

    The tenant row lock keeps `balance_after_cents` truthful, and the unique
    `(message_id, kind)` makes the ledger insert the arbiter: only the caller
    whose insert wins credits the tenant, so duplicate finalisers cannot
    double-refund. Returns False when a REFUND row already exists.
    """
    if amount_cents <= 0:
      raise ValueError("refund amount must be positive")

    balance = await self.db.scalar(
      select(Tenant.balance_cents).where(Tenant.id == tenant_id).with_for_update()
    )
    if balance is None:
      raise ValueError(f"unknown tenant {tenant_id}")

    new_balance = balance + amount_cents
    refunded = await self.db.scalar(
      pg_insert(LedgerEntry)
      .values(
        id=uuid.uuid7(),
        tenant_id=tenant_id,
        message_id=message_id,
        kind=LedgerKind.REFUND,
        amount_cents=amount_cents,
        balance_after_cents=new_balance,
      )
      .on_conflict_do_nothing(index_elements=["message_id", "kind"])
      .returning(LedgerEntry.id)
    )
    if refunded is None:
      return False

    await self.db.execute(
      update(Tenant)
      .where(Tenant.id == tenant_id)
      .values(balance_cents=new_balance)
      .execution_options(synchronize_session=False)
    )
    return True
