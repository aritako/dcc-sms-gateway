import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

GATEWAY_ROOT = Path(__file__).resolve().parents[1]

# Tests never touch the developer's dev database, even when a root .env exists.
TEST_DATABASE_URL = os.environ.get(
  "TEST_DATABASE_URL", "postgresql+asyncpg://sms:sms@localhost:5432/sms_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["DLR_SECRET"] = "test-dlr-secret"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

from app.db.database import dispose_engine, get_engine  # noqa: E402
from app.db.redis import close_redis, get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.settings import get_settings  # noqa: E402


def _ensure_test_database() -> None:
  """Create the shared test database once if missing (`postgres` is the bootstrap db)."""
  url = make_url(TEST_DATABASE_URL)
  if not url.database:
    raise RuntimeError("TEST_DATABASE_URL must include a database name")
  admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
    hide_password=False
  )
  with psycopg.connect(admin_dsn, autocommit=True) as connection:
    exists = connection.execute(
      "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
    ).fetchone()
    if exists is None:
      connection.execute(
        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database))
      )


@pytest.fixture(scope="session")
def migrated_database() -> None:
  """Provision an isolated test database and bring it to `head` once per run."""
  _ensure_test_database()
  config = Config(str(GATEWAY_ROOT / "alembic.ini"))
  config.set_main_option("script_location", str(GATEWAY_ROOT / "migrations"))
  command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
async def _close_clients() -> AsyncIterator[None]:
  yield
  await close_redis()
  await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean_state(migrated_database: None) -> AsyncIterator[None]:
  """Truncate every domain table and the test Redis db after each test."""
  yield
  async with get_engine().begin() as connection:
    tables = (
      (
        await connection.execute(
          text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
          )
        )
      )
      .scalars()
      .all()
    )
    if tables:
      quoted = ", ".join(f'"{table}"' for table in tables)
      await connection.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
  await get_redis().flushdb()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
  """HTTP client that calls the ASGI app in-process (no server, no ports)."""
  async with AsyncClient(
    transport=ASGITransport(app=app), base_url="http://gateway.test"
  ) as client:
    yield client


class FakeCarrier:
  """In-process stand-in for the carrier service (built out in ticket #3)."""

  def __init__(self) -> None:
    self.submits: list[dict[str, object]] = []

  def handler(self, request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path == "/submit":
      payload = json.loads(request.content or b"{}")
      self.submits.append(payload)
      return httpx.Response(202, json={"carrier_id": f"fake-{len(self.submits)}"})
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def fake_carrier() -> FakeCarrier:
  return FakeCarrier()


@pytest.fixture
async def fake_carrier_client(fake_carrier: FakeCarrier) -> AsyncIterator[AsyncClient]:
  """httpx client wired to FakeCarrier instead of the network."""
  transport = httpx.MockTransport(fake_carrier.handler)
  async with httpx.AsyncClient(
    transport=transport, base_url=get_settings().carrier_url
  ) as client:
    yield client
