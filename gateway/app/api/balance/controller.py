from datetime import datetime

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import tenant_for_api_key
from app.db.database import get_session
from app.db.models import LedgerEntry, Tenant

router = APIRouter()


class BalanceResponse(BaseModel):
  # Integer cents throughout.
  balance: int
  opening_balance: int
  ledger_sum: int
  currency: str
  as_of: datetime


@router.get("/balance", response_model=BalanceResponse)
async def balance(
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> BalanceResponse | JSONResponse:
  tenant = await tenant_for_api_key(db, api_key or "")
  if tenant is None:
    return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED"})

  # One statement, so balance, opening balance and the ledger sum share a
  # snapshot. `now()` is the transaction timestamp the row was read at.
  ledger_sum = (
    select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
    .where(LedgerEntry.tenant_id == tenant.id)
    .scalar_subquery()
  )
  row = (
    await db.execute(
      select(
        Tenant.balance_cents,
        Tenant.opening_balance_cents,
        Tenant.currency,
        ledger_sum.label("ledger_sum"),
        func.now().label("as_of"),
      ).where(Tenant.id == tenant.id)
    )
  ).one_or_none()
  if row is None:  # deleted between auth and this read
    return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED"})

  return BalanceResponse(
    balance=row.balance_cents,
    opening_balance=row.opening_balance_cents,
    ledger_sum=row.ledger_sum,
    currency=row.currency,
    as_of=row.as_of,
  )
