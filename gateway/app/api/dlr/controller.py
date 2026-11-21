import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dlr import CARRIER_STATUS_MAP, ingest_dlr
from app.db.database import get_session
from app.settings import get_settings

router = APIRouter()


class DlrPayload(BaseModel):
  carrier_id: str = Field(min_length=1)
  status: str
  timestamp: AwareDatetime


def _signature_matches(secret: str, body: bytes, signature: str | None) -> bool:
  """Hex HMAC-SHA256 over the exact request bytes, compared in constant time."""
  if signature is None:
    return False
  expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
  try:
    return hmac.compare_digest(expected, signature)
  except TypeError:
    # compare_digest rejects non-ASCII str; that can only be a bad signature.
    return False


@router.post("/dlr", response_model=None)
async def receive_dlr(
  request: Request,
  db: AsyncSession = Depends(get_session),
  x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, str] | JSONResponse:
  # Read the raw bytes: FastAPI must never re-serialize before we verify them.
  body = await request.body()
  if not _signature_matches(get_settings().dlr_secret, body, x_signature):
    return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED"})

  try:
    payload = DlrPayload.model_validate_json(body)
    status = CARRIER_STATUS_MAP[payload.status]
  except (ValidationError, KeyError):
    return JSONResponse(status_code=400, content={"error": "INVALID_DLR"})

  result = await ingest_dlr(
    db,
    carrier="default",
    carrier_id=payload.carrier_id,
    status=status,
    occurred_at=payload.timestamp,
  )
  await db.commit()
  return {"result": result}
