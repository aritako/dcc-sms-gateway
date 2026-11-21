from helpers import ADMIN_HEADERS, DEFAULT_KEY, SHOP_KEY, create_tenant, send_message
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

LEDGER_PATH = "/v1/ledger"
BALANCE_PATH = "/v1/balance"


async def _ledger(
  client: AsyncClient, *, key: str = DEFAULT_KEY, headers: dict[str, str] | None = None, **params
):
  return await client.get(LEDGER_PATH, params=params, headers=headers or {"X-API-Key": key})


async def test_ledger_is_tenant_scoped_and_newest_first(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  first = (await send_message(client)).json()["id"]
  second = (await send_message(client)).json()["id"]
  await send_message(client, key=SHOP_KEY)

  response = await _ledger(client)

  assert response.status_code == 200
  body = response.json()
  assert body["limit"] == 50
  assert body["offset"] == 0
  assert body["has_more"] is False
  entries = body["entries"]
  assert [entry["kind"] for entry in entries] == ["CHARGE", "CHARGE"]
  assert [entry["amount"] for entry in entries] == [-3, -3]
  assert [entry["balance_after"] for entry in entries] == [9_994, 9_997]
  assert [entry["message_id"] for entry in entries] == [second, first]
  assert all("created_at" in entry for entry in entries)


async def test_ledger_paginates_with_offset_and_has_more(client: AsyncClient, tenant) -> None:
  for _ in range(3):
    assert (await send_message(client)).status_code == 202

  first = (await _ledger(client, limit=2)).json()
  second = (await _ledger(client, limit=2, offset=2)).json()

  assert len(first["entries"]) == 2
  assert first["has_more"] is True
  assert len(second["entries"]) == 1
  assert second["has_more"] is False
  assert (await _ledger(client, limit=201)).status_code == 422


async def test_balance_matches_the_ledger(client: AsyncClient, tenant) -> None:
  await send_message(client)
  await send_message(client)

  balance = (await client.get(BALANCE_PATH, headers={"X-API-Key": DEFAULT_KEY})).json()
  entries = (await _ledger(client)).json()["entries"]

  assert balance["balance"] == 9_994
  assert sum(entry["amount"] for entry in entries) == -6
  assert balance["balance"] == balance["opening_balance"] + sum(
    entry["amount"] for entry in entries
  )
  # The newest row's `balance_after` is the current balance.
  assert entries[0]["balance_after"] == balance["balance"]


async def test_admin_reads_every_tenants_entries(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  await send_message(client)
  await send_message(client, key=SHOP_KEY)
  await send_message(client, key=SHOP_KEY)

  across = (await _ledger(client, headers=ADMIN_HEADERS)).json()
  scoped = (await _ledger(client, headers=ADMIN_HEADERS, tenant_id=str(other.id))).json()

  assert len(across["entries"]) == 3
  assert len(scoped["entries"]) == 2
