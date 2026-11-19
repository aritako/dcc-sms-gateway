from uuid import UUID

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey
from app.security import hash_api_key


class MessagesHandler:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def validate_message(self, api_key: str) -> UUID | None:
    """Return the tenant that owns `api_key`, or None when the key is unknown."""
    if not api_key:
      return None
    return await self.db.scalar(
      select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_api_key(api_key))
    )

  def error_response(self, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})
