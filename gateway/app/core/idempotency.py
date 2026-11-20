import hashlib
import json
import uuid
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey


class IdempotencyService:
  """Reserve, replay and complete one response per (tenant, Idempotency-Key)."""

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

  def replay(self, stored: IdempotencyKey, fingerprint: str) -> JSONResponse:
    if stored.request_hash != fingerprint:
      return JSONResponse(status_code=409, content={"error": "IDEMPOTENCY_KEY_REUSED"})
    # complete() shares the winner's transaction, so a committed row is complete.
    assert stored.response_status is not None and stored.response_body is not None
    return JSONResponse(status_code=stored.response_status, content=stored.response_body)

  async def claim(self, tenant_id: uuid.UUID, key: str, request_hash: str) -> IdempotencyKey | None:
    """Reserve the key (returns None), or return the row already stored for it.

    Must be the first write of the transaction: a duplicate blocks on the primary
    key here until this transaction ends, then replays it or reserves the key itself.
    """
    reserved = await self.db.scalar(
      pg_insert(IdempotencyKey)
      .values(tenant_id=tenant_id, key=key, request_hash=request_hash)
      .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
      .returning(IdempotencyKey.key)
    )
    if reserved is not None:
      return None
    return await self.find(tenant_id, key)  # new statement, so it sees the winner's commit

  async def complete(
    self, tenant_id: uuid.UUID, key: str, status_code: int, body: dict[str, Any]
  ) -> None:
    await self.db.execute(
      update(IdempotencyKey)
      .where(IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key)
      .values(response_status=status_code, response_body=body)
    )
