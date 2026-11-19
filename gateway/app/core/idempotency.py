import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey


class IdempotencyService:
  """Store and look up one accepted response per (tenant, Idempotency-Key).

  Only 2xx responses are stored (there is no TTL cleanup yet): a failed send
  leaves no row, so the client can retry the same key after fixing the cause.
  """

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  def request_hash(self, method: str, path: str, payload: dict[str, Any]) -> str:
    """Hash method, path and canonical JSON (sorted keys, stable separators)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    material = f"{method}\n{path}\n{canonical}".encode()
    return hashlib.sha256(material).hexdigest()

  async def find(self, tenant_id: uuid.UUID, key: str) -> IdempotencyKey | None:
    return await self.db.scalar(
      select(IdempotencyKey).where(
        IdempotencyKey.tenant_id == tenant_id,
        IdempotencyKey.key == key,
      )
    )

  async def store(
    self,
    tenant_id: uuid.UUID,
    key: str,
    request_hash: str,
    status_code: int,
    body: dict[str, Any],
  ) -> None:
    """Add the snapshot and flush, so a duplicate key raises IntegrityError here."""
    self.db.add(
      IdempotencyKey(
        tenant_id=tenant_id,
        key=key,
        request_hash=request_hash,
        response_status=status_code,
        response_body=body,
      )
    )
    await self.db.flush()
