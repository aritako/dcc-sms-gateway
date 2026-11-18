from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.dlr.client import GatewayDLRClient
from app.dlr.handler import DeliveryReceiptSender
from app.rate_limit import limiter
from app.settings import get_settings
from app.submit import controller

# DLR callbacks are best effort; never let a stalled gateway hang the carrier.
DLR_TIMEOUT = httpx.Timeout(10.0, connect=2.0)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
  settings = get_settings()
  async with httpx.AsyncClient(timeout=DLR_TIMEOUT) as http:
    app.state.dlr_sender = DeliveryReceiptSender(
      GatewayDLRClient(http, settings.gateway_url, settings.dlr_secret)
    )
    try:
      yield
    finally:
      await app.state.dlr_sender.aclose()


app = FastAPI(title="DCC SMS Gateway", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(controller.router, prefix="/submit", tags=["submit"])


@app.get("/")
async def root():
  return {"message": "Hello World"}
