import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LedgerEntry, LedgerKind, Tenant


class InsufficientBalance(Exception):
  """The tenant's balance cannot cover the charge."""


class BillingService:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def charge(self, tenant_id: uuid.UUID, message_id: uuid.UUID, amount_cents: int) -> int:
    """Debit the tenant and write the CHARGE row."""
    if amount_cents <= 0:
      raise ValueError("charge amount must be positive")

    balance_after = await self.db.scalar(
      update(Tenant)
      .where(Tenant.id == tenant_id, Tenant.balance_cents >= amount_cents)
      .values(balance_cents=Tenant.balance_cents - amount_cents)
      .returning(Tenant.balance_cents)
      # Plain SQL statement; don't let the ORM touch in-memory Tenant objects.
      .execution_options(synchronize_session=False)
    )
    if balance_after is None:
      raise InsufficientBalance(tenant_id)

    self.db.add(
      LedgerEntry(
        tenant_id=tenant_id,
        message_id=message_id,
        kind=LedgerKind.CHARGE,
        amount_cents=-amount_cents,
        balance_after_cents=balance_after,
      )
    )
    await self.db.flush()
    return balance_after
