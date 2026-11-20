import uuid

from sqlalchemy import update, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LedgerEntry, LedgerKind, Tenant


class InsufficientBalance(Exception):
  """The tenant's balance cannot cover the charge."""


class BillingService:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def charge(self, tenant_id: uuid.UUID, message_id: uuid.UUID, amount_cents: int) -> int:
    return await self.charge_many(tenant_id=tenant_id, charges=[(message_id, amount_cents)])
  
  async def charge_many(self, tenant_id : uuid.UUID, charges: list[tuple[uuid.UUID, int]]):
    """Update balances in batch for a Tenant. Needs to be called after message is inserted"""
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
