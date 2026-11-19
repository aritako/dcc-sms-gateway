from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import ApiKey, Price, Tenant
from app.security import hash_api_key

BALANCE_PATH = "/v1/balance"
SEND_PATH = "/v1/messages"
API_KEY = "balance-test-key"
MESSAGE = {"to": "+8801712345678", "from": "ACME", "text": "hello"}


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
  async for session in get_session():
    yield session


async def _create_tenant(
  session: AsyncSession,
  *,
  name: str = "Acme",
  api_key: str = API_KEY,
  balance_cents: int = 10_000,
) -> Tenant:
  tenant = Tenant(
    name=name,
    opening_balance_cents=balance_cents,
    balance_cents=balance_cents,
  )
  session.add(tenant)
  await session.flush()
  session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(api_key)))
  await session.commit()
  return tenant


@pytest.fixture
async def tenant(session: AsyncSession) -> Tenant:
  tenant = await _create_tenant(session)
  # "hello" is one GSM-7 segment, so a send costs 3 cents.
  session.add(Price(prefix="+880", price_per_segment_cents=3))
  await session.commit()
  return tenant


async def _balance(client: AsyncClient, key: str = API_KEY) -> Response:
  return await client.get(BALANCE_PATH, headers={"X-API-Key": key})


async def test_missing_key_is_unauthorized(client: AsyncClient) -> None:
  response = await client.get(BALANCE_PATH)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_unknown_key_is_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await _balance(client, "not-a-real-key")

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_reports_the_opening_balance_in_integer_cents(
  client: AsyncClient, tenant: Tenant
) -> None:
  response = await _balance(client)

  assert response.status_code == 200
  body = response.json()
  assert body == {
    "balance": 10_000,
    "opening_balance": 10_000,
    "ledger_sum": 0,
    "currency": "USD",
    "as_of": body["as_of"],
  }
  assert isinstance(body["balance"], int)
  assert isinstance(body["opening_balance"], int)
  assert isinstance(body["ledger_sum"], int)
  assert datetime.fromisoformat(body["as_of"]).tzinfo is not None


async def test_charge_is_visible_immediately_and_matches_the_ledger(
  client: AsyncClient, tenant: Tenant
) -> None:
  sent = await client.post(SEND_PATH, json=MESSAGE, headers={"X-API-Key": API_KEY})
  assert sent.status_code == 202

  body = (await _balance(client)).json()

  assert body["balance"] == 9_997
  assert body["opening_balance"] == 10_000
  assert body["ledger_sum"] == -3
  assert body["balance"] == body["opening_balance"] + body["ledger_sum"]


async def test_balance_is_tenant_scoped(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  await _create_tenant(session, name="Other", api_key="other-key", balance_cents=500)

  sent = await client.post(SEND_PATH, json=MESSAGE, headers={"X-API-Key": API_KEY})
  assert sent.status_code == 202

  other = (await _balance(client, "other-key")).json()
  assert other["balance"] == 500
  assert other["opening_balance"] == 500
  assert other["ledger_sum"] == 0
