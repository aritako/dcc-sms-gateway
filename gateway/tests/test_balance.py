from datetime import datetime

from helpers import ADMIN_HEADERS, DEFAULT_KEY, MESSAGE, create_tenant
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

BALANCE_PATH = "/v1/balance"
SEND_PATH = "/v1/messages"


async def _balance(client: AsyncClient, key: str = DEFAULT_KEY) -> Response:
  return await client.get(BALANCE_PATH, headers={"X-API-Key": key})


async def test_reports_the_opening_balance_in_integer_cents(client: AsyncClient, tenant) -> None:
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
  client: AsyncClient, tenant
) -> None:
  sent = await client.post(SEND_PATH, json=MESSAGE, headers={"X-API-Key": DEFAULT_KEY})
  assert sent.status_code == 202

  body = (await _balance(client)).json()

  assert body["balance"] == 9_997
  assert body["opening_balance"] == 10_000
  assert body["ledger_sum"] == -3
  assert body["balance"] == body["opening_balance"] + body["ledger_sum"]


async def test_balance_is_tenant_scoped(client: AsyncClient, session: AsyncSession, tenant) -> None:
  await create_tenant(session, name="Other", api_key="other-key", balance_cents=500)

  sent = await client.post(SEND_PATH, json=MESSAGE, headers={"X-API-Key": DEFAULT_KEY})
  assert sent.status_code == 202

  other = (await _balance(client, "other-key")).json()
  assert other["balance"] == 500
  assert other["opening_balance"] == 500
  assert other["ledger_sum"] == 0


async def test_admin_can_read_another_tenants_balance(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="Other", api_key="other-key", balance_cents=500)

  response = await client.get(
    BALANCE_PATH, params={"tenant_id": str(other.id)}, headers=ADMIN_HEADERS
  )

  assert response.status_code == 200
  body = response.json()
  assert body["balance"] == 500
  assert body["opening_balance"] == 500
  assert body["ledger_sum"] == 0


async def test_admin_balance_requires_a_tenant(client: AsyncClient, tenant) -> None:
  response = await client.get(BALANCE_PATH, headers=ADMIN_HEADERS)

  assert response.status_code == 422
  assert response.json() == {"error": "TENANT_ID_REQUIRED"}
