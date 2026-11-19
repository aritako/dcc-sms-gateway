import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import store
from app.dlr.client import GatewayDLRClient
from app.dlr.handler import DeliveryReceiptSender
from app.rate_limit import limiter
from app.settings import get_settings
from app.submit import controller

# DLR callbacks are best effort; never let a stalled gateway hang the carrier.
DLR_TIMEOUT = httpx.Timeout(10.0, connect=2.0)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
  if settings.seed is not None:
    random.seed(settings.seed)
  async with httpx.AsyncClient(timeout=DLR_TIMEOUT) as http:
    app.state.dlr_sender = DeliveryReceiptSender(
      GatewayDLRClient(http, settings.gateway_url, settings.dlr_secret)
    )
    try:
      yield
    finally:
      await app.state.dlr_sender.aclose()


def _rate_limited(request: Request, exc: RateLimitExceeded):
  store.counters["s429"] += 1
  return _rate_limit_exceeded_handler(request, exc)


app = FastAPI(title="DCC SMS Gateway", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limited)
app.include_router(controller.router, prefix="/submit", tags=["submit"])


@app.middleware("http")
async def count_submits(request: Request, call_next):
  # Count before the rate limiter can reject, so /stats sees every attempt.
  if request.url.path == "/submit":
    store.note_request()
  return await call_next(request)


@app.get("/healthz")
async def healthz():
  return {"status": "ok"}


@app.get("/stats")
async def stats():
  # Consumed by the load script (#20).
  return store.snapshot()
