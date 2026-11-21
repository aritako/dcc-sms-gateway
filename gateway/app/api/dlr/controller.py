from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dlr import CARRIER_STATUS_MAP, ingest_dlr
from app.db.database import get_session
from app.settings import get_settings

from .handler import signature_matches
from .models import DlrPayload

router = APIRouter()


@router.post("/dlr", response_model=None)
async def receive_dlr(
  request: Request,
  db: AsyncSession = Depends(get_session),
  x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, str] | JSONResponse:
  # Read the raw bytes: FastAPI must never re-serialize before we verify them.
  body = await request.body()
  if not signature_matches(get_settings().dlr_secret, body, x_signature):
    return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED"})

  try:
    payload = DlrPayload.model_validate_json(body)
  except ValidationError:
    return JSONResponse(status_code=400, content={"error": "INVALID_DLR"})

  if payload.status not in CARRIER_STATUS_MAP:
    return JSONResponse(status_code=400, content={"error": "INVALID_DLR"})

  result = await ingest_dlr(
    db,
    carrier="default",
    carrier_id=payload.carrier_id,
    status=CARRIER_STATUS_MAP[payload.status],
    occurred_at=payload.timestamp,
  )
  await db.commit()
  return {"result": result}
