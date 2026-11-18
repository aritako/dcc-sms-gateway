import asyncio, random
from fastapi import APIRouter, Request, HTTPException
from .model import SubmitRequest, SubmitResponse
from app.rate_limit import limiter
from app.settings import get_settings

router = APIRouter()
settings = get_settings()

@router.post("")
@limiter.limit(f"{settings.rate_limit_submit_per_sec}/second")
async def submit(request: Request, payload: SubmitRequest) -> SubmitResponse:

  # Random delay between 20ms and 300ms, as per spec
  await asyncio.sleep(random.uniform(settings.latency_min, settings.latency_max))

  if random.random() < settings.p_503:
    # 3% chance of 503
    raise HTTPException(status_code=503, detail="TEMPORARY_FAILURE")
  
  if random.random() < settings.p_400:
    # 1% chance of 400
    raise HTTPException(status_code=400, detail="INVALID_DESTINATION")

  return SubmitResponse(carrier_id="carrier-id-123")
