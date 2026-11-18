import asyncio, random
from fastapi import APIRouter, HTTPException
from .model import SubmitRequest
from fastapi.responses import JSONResponse
router = APIRouter()

@router.post("/")
async def submit(request: SubmitRequest):

  # Random delay between 20ms and 300ms, as per spec
  await asyncio.sleep(random.uniform(0.02, 0.3))

  return JSONResponse(
    status_code=200,
    content={"carrier_id": request.message_id}
  )

