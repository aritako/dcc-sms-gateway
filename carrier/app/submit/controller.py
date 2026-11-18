import asyncio, random
from fastapi import APIRouter, Depends, HTTPException, Query
from .model import SubmitRequest
from fastapi.responses import JSONResponse
router = APIRouter()

@router.post("/")
def submit(request: SubmitRequest):

  # Random delay between 20ms and 300ms, as per spec
  delay = asyncio.sleep(random.uniform(20, 300))

  return JSONResponse(
    status_code=200,
    content={"message_id": request.message_id}
  )

