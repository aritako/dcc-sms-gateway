from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import read_scope
from app.db.database import get_session
from app.db.models import Message, MessageStatus, Tenant

from .models import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def message_stats(
  db: AsyncSession = Depends(get_session),
  tenant: Tenant | None = Depends(read_scope),
) -> StatsResponse:
  statement = select(Message.status, func.count()).group_by(Message.status)
  if tenant is not None:
    statement = statement.where(Message.tenant_id == tenant.id)

  counts = {status.value: 0 for status in MessageStatus}
  for status, count in (await db.execute(statement)).all():
    counts[status] = count
  return StatsResponse(counts=counts)
