import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import LedgerKind


class LedgerEntryOut(BaseModel):
  id: uuid.UUID
  message_id: uuid.UUID | None
  kind: LedgerKind
  # Integer cents, like every other money field.
  amount: int
  balance_after: int
  created_at: datetime


class LedgerPage(BaseModel):
  entries: list[LedgerEntryOut]
  limit: int
  offset: int
  has_more: bool
