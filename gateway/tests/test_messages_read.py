from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from .helpers import ADMIN_HEADERS, create_tenant, send_batch, send_message, set_message
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessageStatus

LIST_PATH = "/v1/messages"
ACME_KEY = "acme-key"
SHOP_KEY = "shop-key"


@pytest.fixture
async def tenant(session: AsyncSession):
  return await create_tenant(session, name="Acme Bank", api_key=ACME_KEY)


async def _list(
  client: AsyncClient, *, key: str = ACME_KEY, headers: dict[str, str] | None = None, **params
):
  return await client.get(LIST_PATH, params=params, headers=headers or {"X-API-Key": key})


async def _detail(client: AsyncClient, message_id: str, *, headers: dict[str, str] | None = None):
  return await client.get(f"{LIST_PATH}/{message_id}", headers=headers or {"X-API-Key": ACME_KEY})


def _refs(body: dict) -> list[str]:
  return [message["client_ref"] for message in body["messages"]]


async def test_list_returns_newest_first_summaries_without_text(
  client: AsyncClient, tenant
) -> None:
  for ref in ("first", "second", "third"):
    assert (await send_message(client, key=ACME_KEY, client_ref=ref)).status_code == 202

  response = await _list(client)

  assert response.status_code == 200
  body = response.json()
  assert body["limit"] == 50
  assert body["offset"] == 0
  assert body["has_more"] is False
  assert _refs(body) == ["third", "second", "first"]

  summary = body["messages"][0]
  assert set(summary) == {
    "id",
    "tenant_id",
    "tenant_name",
    "to",
    "from",
    "status",
    "segments",
    "price_per_segment",
    "total_price",
    "currency",
    "client_ref",
    "batch_id",
    "created_at",
  }
  assert summary["tenant_name"] == "Acme Bank"
  assert summary["from"] == "ACME"
  assert summary["to"] == "+8801712345678"
  assert summary["status"] == "ACCEPTED"
  assert summary["segments"] == 1
  assert summary["price_per_segment"] == 3
  assert summary["total_price"] == 3
  assert summary["currency"] == "USD"
  assert "text" not in summary


async def test_list_is_tenant_scoped(client: AsyncClient, session: AsyncSession, tenant) -> None:
  await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  await send_message(client, key=ACME_KEY, client_ref="acme")
  await send_message(client, key=SHOP_KEY, client_ref="shop")

  acme = (await _list(client)).json()
  shop = (await _list(client, key=SHOP_KEY)).json()

  assert _refs(acme) == ["acme"]
  assert _refs(shop) == ["shop"]


async def test_list_defaults_to_50_and_rejects_limits_outside_1_to_200(
  client: AsyncClient, session: AsyncSession
) -> None:
  await create_tenant(session, api_key=ACME_KEY)
  assert (await send_batch(client, 55, key=ACME_KEY)).status_code == 202

  first_page = (await _list(client)).json()
  assert len(first_page["messages"]) == 50
  assert first_page["has_more"] is True

  full_page = (await _list(client, limit=200)).json()
  assert len(full_page["messages"]) == 55
  assert full_page["has_more"] is False

  assert (await _list(client, limit=0)).status_code == 422
  assert (await _list(client, limit=201)).status_code == 422


async def test_list_filters_by_status(client: AsyncClient, session: AsyncSession, tenant) -> None:
  delivered = (await send_message(client, key=ACME_KEY, client_ref="delivered")).json()["id"]
  await send_message(client, key=ACME_KEY, client_ref="accepted")
  await set_message(session, UUID(delivered), status=MessageStatus.DELIVERED)

  body = (await _list(client, status="DELIVERED")).json()
  assert _refs(body) == ["delivered"]
  assert (await _list(client, status="FAILED")).json()["messages"] == []
  assert (await _list(client, status="NOT_A_STATUS")).status_code == 422


async def test_list_filters_by_client_ref(client: AsyncClient, tenant) -> None:
  await send_message(client, key=ACME_KEY, client_ref="order-42")
  await send_message(client, key=ACME_KEY, client_ref="order-43")

  assert _refs((await _list(client, client_ref="order-42")).json()) == ["order-42"]
  # Exact match: a prefix search is not silently widened to a LIKE scan.
  assert (await _list(client, client_ref="order")).json()["messages"] == []


async def test_list_filters_by_inclusive_date_range(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  message_ids = []
  for ref in ("a", "b", "c"):
    message_ids.append((await send_message(client, key=ACME_KEY, client_ref=ref)).json()["id"])

  base = datetime(2026, 1, 1, tzinfo=UTC)
  for index, message_id in enumerate(message_ids):
    await set_message(session, UUID(message_id), created_at=base + timedelta(hours=index))

  after = (await _list(client, created_after=(base + timedelta(hours=1)).isoformat())).json()
  assert _refs(after) == ["c", "b"]

  before = (await _list(client, created_before=(base + timedelta(hours=1)).isoformat())).json()
  assert _refs(before) == ["b", "a"]

  at_bound = (
    await _list(
      client,
      created_after=(base + timedelta(hours=1)).isoformat(),
      created_before=(base + timedelta(hours=1)).isoformat(),
    )
  ).json()
  assert _refs(at_bound) == ["b"]

  assert (await _list(client, created_after="not-a-date")).status_code == 422


async def test_list_paginates_with_offset_and_has_more(client: AsyncClient, tenant) -> None:
  for ref in ("a", "b", "c"):
    await send_message(client, key=ACME_KEY, client_ref=ref)

  first = (await _list(client, limit=2)).json()
  second = (await _list(client, limit=2, offset=2)).json()

  assert _refs(first) == ["c", "b"]
  assert first["has_more"] is True
  assert _refs(second) == ["a"]
  assert second["has_more"] is False
  assert set(message["id"] for message in first["messages"]).isdisjoint(
    message["id"] for message in second["messages"]
  )


async def test_detail_returns_the_message_and_event_history(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  sent = (await send_message(client, key=ACME_KEY, client_ref="detail-1")).json()
  await set_message(session, UUID(sent["id"]), status=MessageStatus.DELIVERED)

  response = await _detail(client, sent["id"])

  assert response.status_code == 200
  body = response.json()
  assert body["id"] == sent["id"]
  assert body["text"] == "hello"
  assert body["client_ref"] == "detail-1"
  assert body["encoding"] == "GSM7"
  assert body["status"] == "DELIVERED"
  assert body["tenant_name"] == "Acme Bank"
  assert body["currency"] == "USD"
  assert "attempts" not in body
  assert [event["status"] for event in body["events"]] == ["ACCEPTED", "DELIVERED"]
  event_times = [event["created_at"] for event in body["events"]]
  assert event_times == sorted(event_times)


async def test_another_tenants_message_and_unknown_ids_are_404(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  shop_message = (await send_message(client, key=SHOP_KEY)).json()

  cross_tenant = await _detail(client, shop_message["id"])
  unknown = await _detail(client, str(uuid4()))

  assert cross_tenant.status_code == 404
  assert cross_tenant.json() == {"error": "NOT_FOUND"}
  assert unknown.status_code == 404
  assert unknown.json() == {"error": "NOT_FOUND"}


async def test_admin_lists_across_tenants_and_can_scope_to_one(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  await send_message(client, key=ACME_KEY, client_ref="acme")
  await send_message(client, key=SHOP_KEY, client_ref="shop")

  across = (await _list(client, headers=ADMIN_HEADERS)).json()
  assert {message["tenant_name"] for message in across["messages"]} == {
    "Acme Bank",
    "ShopFast",
  }

  scoped = (await _list(client, headers=ADMIN_HEADERS, tenant_id=str(other.id))).json()
  assert _refs(scoped) == ["shop"]


async def test_admin_detail_crosses_tenants_unless_tenant_id_scopes_it(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)
  shop_message = (await send_message(client, key=SHOP_KEY)).json()

  across = await _detail(client, shop_message["id"], headers=ADMIN_HEADERS)
  scoped = await client.get(
    f"{LIST_PATH}/{shop_message['id']}",
    params={"tenant_id": str(tenant.id)},
    headers=ADMIN_HEADERS,
  )

  assert across.status_code == 200
  assert across.json()["tenant_name"] == "ShopFast"
  assert scoped.status_code == 404
  assert scoped.json() == {"error": "NOT_FOUND"}


async def test_a_tenant_key_can_never_pass_tenant_id(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  other = await create_tenant(session, name="ShopFast", api_key=SHOP_KEY)

  foreign = await _list(client, tenant_id=str(other.id))
  own = await _list(client, tenant_id=str(tenant.id))

  assert foreign.status_code == 403
  assert foreign.json() == {"error": "FORBIDDEN"}
  assert own.status_code == 403
  assert own.json() == {"error": "FORBIDDEN"}


async def test_a_wrong_admin_key_fails_closed_even_with_a_valid_tenant_key(
  client: AsyncClient, tenant
) -> None:
  response = await _list(client, headers={"X-API-Key": ACME_KEY, "X-Admin-Key": "wrong-admin-key"})

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_admin_naming_an_unknown_tenant_is_404(client: AsyncClient, tenant) -> None:
  response = await _list(client, headers=ADMIN_HEADERS, tenant_id=str(uuid4()))

  assert response.status_code == 404
  assert response.json() == {"error": "NOT_FOUND"}
