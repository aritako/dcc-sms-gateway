from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import ApiKey, Tenant
from app.security import hash_api_key

PLAINTEXT_KEY = "dcc_test_api_key"
MESSAGE = {"to": "+8801712345678", "from": "+15550001111", "text": "hello"}


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
  async for session in get_session():
    yield session


@pytest.fixture
async def tenant_with_key(session: AsyncSession) -> Tenant:
  tenant = Tenant(name="Test Tenant", opening_balance_cents=10_000, balance_cents=10_000)
  session.add(tenant)
  await session.flush()
  session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(PLAINTEXT_KEY)))
  await session.commit()
  return tenant


async def test_missing_key_is_unauthorized(client: AsyncClient) -> None:
  response = await client.post("/v1/messages", json=MESSAGE)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_unknown_key_is_unauthorized(client: AsyncClient, tenant_with_key: Tenant) -> None:
  response = await client.post(
    "/v1/messages", json=MESSAGE, headers={"X-API-Key": "not-a-real-key"}
  )

  assert response.status_code == 401


async def test_valid_key_is_accepted(client: AsyncClient, tenant_with_key: Tenant) -> None:
  response = await client.post(
    "/v1/messages", json=MESSAGE, headers={"X-API-Key": PLAINTEXT_KEY}
  )

  assert response.status_code == 200
  assert response.json()["message_id"]
