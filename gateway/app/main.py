import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.messages import controller
from app.db.database import dispose_engine, get_engine
from app.db.redis import close_redis, get_redis
from app.settings import get_settings

HEALTHCHECK_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
  # Startup validation: fail fast on missing config; imports stay side-effect free.
  get_settings()
  try:
    yield
  finally:
    # Shutdown: return pooled connections.
    await close_redis()
    await dispose_engine()


app = FastAPI(title="DCC SMS Gateway", lifespan=lifespan)
v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(controller.router, prefix="/messages", tags=["messages"])
app.include_router(v1_router)


@app.get("/")
async def root() -> dict[str, str]:
  return {"message": "Hello World"}


async def _ping_db() -> None:
  async with get_engine().connect() as connection:
    await connection.execute(text("SELECT 1"))


async def _ping_redis() -> None:
  await get_redis().ping()


@app.get("/healthz")
async def healthz() -> JSONResponse:
  status: dict[str, str] = {}
  checks = {"db": _ping_db, "redis": _ping_redis}
  for name, ping in checks.items():
    try:
      await asyncio.wait_for(ping(), timeout=HEALTHCHECK_TIMEOUT_SECONDS)
    except Exception:
      status[name] = "error"
    else:
      status[name] = "ok"
  unhealthy = "error" in status.values()
  return JSONResponse(status_code=503 if unhealthy else 200, content=status)
