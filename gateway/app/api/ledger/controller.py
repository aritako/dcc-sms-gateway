from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import read_scope
from app.db.database import get_session
from app.db.models import LedgerEntry, Tenant

from .models import LedgerEntryOut, LedgerPage

router = APIRouter()


@router.get("/ledger", response_model=LedgerPage)
async def list_ledger(
  db: AsyncSession = Depends(get_session),
  tenant: Tenant | None = Depends(read_scope),
  limit: int = Query(default=50, ge=1, le=200),
  offset: int = Query(default=0, ge=0),
) -> LedgerPage:
  statement = select(LedgerEntry)
  if tenant is not None:
    statement = statement.where(LedgerEntry.tenant_id == tenant.id)
  statement = (
    statement.order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
    .limit(limit + 1)
    .offset(offset)
  )

  rows = (await db.scalars(statement)).all()
  return LedgerPage(
    entries=[
      LedgerEntryOut(
        id=entry.id,
        message_id=entry.message_id,
        kind=entry.kind,
        amount=entry.amount_cents,
        balance_after=entry.balance_after_cents,
        created_at=entry.created_at,
      )
      for entry in rows[:limit]
    ],
    limit=limit,
    offset=offset,
    has_more=len(rows) > limit,
  )
