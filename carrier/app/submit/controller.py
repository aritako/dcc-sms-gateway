import asyncio, random
from fastapi import APIRouter, Request
from .model import SubmitRequest
from fastapi.responses import JSONResponse
from app.rate_limit import limiter
from app.settings import get_settings
router = APIRouter()

@router.post("")
@limiter.limit("50/second")
async def submit(request: Request, payload: SubmitRequest):

  # Random delay between 20ms and 300ms, as per spec
  await asyncio.sleep(random.uniform(0.02, 0.3))

  return JSONResponse(
    status_code=200,
    content={"carrier_id": payload.message_id}
  )
