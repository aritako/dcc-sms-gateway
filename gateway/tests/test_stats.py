from uuid import UUID

from helpers import ADMIN_HEADERS, DEFAULT_KEY, SHOP_KEY, create_tenant, send_message, set_message
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessageStatus

STATS_PATH = "/v1/stats"
ALL_STATUSES = {status.value: 0 for status in MessageStatus}


async def _stats(
  client: AsyncClient, *, key: str = DEFAULT_KEY, headers: dict[str, str] | None = None, **params
):
  return await client.get(STATS_PATH, params=params, headers=headers or {"X-API-Key": key})


async def test_stats_zero_fills_every_status(client: AsyncClient, tenant) -> None:
  response = await _stats(client)

  assert response.status_code == 200
  assert response.json() == {"counts": ALL_STATUSES}


async def test_stats_counts_each_status_for_the_tenant(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  delivered = (await send_message(client)).json()["id"]
  failed = (await send_message(client)).json()["id"]
  await send_message(client)
  await set_message(session, UUID(delivered), status=MessageStatus.DELIVERED)
  await set_message(session, UUID(failed), status=MessageStatus.FAILED)

  await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  await send_message(client, key=SHOP_KEY)

  own = (await _stats(client)).json()
  shop = (await _stats(client, key=SHOP_KEY)).json()

  assert own == {
    "counts": {
      **ALL_STATUSES,
      "ACCEPTED": 1,
      "DELIVERED": 1,
      "FAILED": 1,
    }
  }
  assert shop == {"counts": {**ALL_STATUSES, "ACCEPTED": 1}}


async def test_admin_stats_cover_all_tenants_or_one(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  await send_message(client)
  await send_message(client)
  await send_message(client, key=SHOP_KEY)

  across = (await _stats(client, headers=ADMIN_HEADERS)).json()
  scoped = (await _stats(client, headers=ADMIN_HEADERS, tenant_id=str(other.id))).json()

  assert across["counts"]["ACCEPTED"] == 3
  assert scoped["counts"]["ACCEPTED"] == 1
