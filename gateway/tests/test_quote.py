from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import ApiKey, Encoding, LedgerEntry, Message, MessageEvent, Price, Tenant
from app.security import hash_api_key

TENANT_KEY = "test-tenant-key"
ADMIN_KEY = "test-admin-key"  # matches ADMIN_API_KEY in conftest
QUOTE_PATH = "/v1/messages/quote"
BANGLADESH = "+8801712345678"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
  async for session in get_session():
    yield session


async def _create_tenant(
  session: AsyncSession,
  *,
  name: str = "Acme",
  currency: str = "USD",
  api_key: str | None = TENANT_KEY,
) -> Tenant:
  tenant = Tenant(
    name=name,
    opening_balance_cents=10_000,
    balance_cents=10_000,
    currency=currency,
  )
  session.add(tenant)
  await session.flush()
  if api_key is not None:
    session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(api_key)))
  await session.commit()
  return tenant


async def _add_price(session: AsyncSession, prefix: str, cents: int) -> None:
  session.add(Price(prefix=prefix, price_per_segment_cents=cents))
  await session.commit()


@pytest.fixture
async def tenant(session: AsyncSession) -> Tenant:
  tenant = await _create_tenant(session)
  await _add_price(session, "+880", 3)
  await _add_price(session, "+971", 5)
  return tenant


async def _quote(
  client: AsyncClient,
  *,
  to: str = BANGLADESH,
  text: str = "hello",
  headers: dict[str, str] | None = None,
  **fields: object,
) -> Response:
  return await client.post(
    QUOTE_PATH,
    json={"to": to, "text": text, **fields},
    headers={"x-api-key": TENANT_KEY} if headers is None else headers,
  )


async def test_quote_reports_encoding_segments_and_integer_cents(
  client: AsyncClient, tenant: Tenant
) -> None:
  response = await _quote(client)

  assert response.status_code == 200
  body = response.json()
  assert body == {
    "encoding": "GSM7",
    "segments": 1,
    "price_per_segment": 3,
    "total_price": 3,
    "currency": "USD",
  }
  # 3.0 == 3 in Python, so the equality above does not prove integer cents.
  assert isinstance(body["price_per_segment"], int)
  assert isinstance(body["total_price"], int)


async def test_encoding_values_are_the_carrier_wire_enum(
  client: AsyncClient, tenant: Tenant
) -> None:
  assert {member.value for member in Encoding} == {"GSM7", "UCS2"}

  response = await _quote(client)

  assert response.json()["encoding"] == Encoding.GSM7.value


async def test_multiple_segments_multiply_the_price(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, text="a" * 161)

  assert response.json()["segments"] == 2
  assert response.json()["total_price"] == 6


async def test_ucs2_text_quotes_per_segment(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, text="😀" * 36)

  assert response.json()["encoding"] == "UCS2"
  assert response.json()["segments"] == 2
  assert response.json()["total_price"] == 6


async def test_longest_matching_prefix_wins(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  await _add_price(session, "+8801", 9)

  assert (await _quote(client, to="+8801712345678")).json()["total_price"] == 9
  assert (await _quote(client, to="+8809123456789")).json()["total_price"] == 3


async def test_currency_comes_from_the_tenant(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  await _create_tenant(session, name="Euro Bank", currency="EUR", api_key="eur-key")

  response = await _quote(client, headers={"x-api-key": "eur-key"})

  assert response.json()["currency"] == "EUR"


async def test_admin_key_quotes_for_the_named_tenant(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, headers={"x-admin-key": ADMIN_KEY}, tenant_id=str(tenant.id))

  assert response.status_code == 200
  assert response.json()["currency"] == "USD"


async def test_admin_key_requires_a_tenant_id(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, headers={"x-admin-key": ADMIN_KEY})

  assert response.status_code == 422
  assert response.json() == {"error": "TENANT_ID_REQUIRED"}


async def test_admin_key_rejects_an_unknown_tenant(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(
    client,
    headers={"x-admin-key": ADMIN_KEY},
    tenant_id="00000000-0000-0000-0000-000000000000",
  )

  assert response.status_code == 404


async def test_wrong_admin_key_is_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, headers={"x-admin-key": "wrong"}, tenant_id=str(tenant.id))

  assert response.status_code == 401


async def test_missing_credentials_are_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, headers={})

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_unknown_api_key_is_unauthorized(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, headers={"x-api-key": "nope"})

  assert response.status_code == 401


async def test_unknown_destination_is_unsupported(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, to="+33123456789")

  assert response.status_code == 422
  assert response.json() == {"error": "UNSUPPORTED_DESTINATION"}


async def test_empty_text_is_rejected(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, text="")

  assert response.status_code == 422


async def test_malformed_destination_is_rejected(client: AsyncClient, tenant: Tenant) -> None:
  response = await _quote(client, to="08801712345678")

  assert response.status_code == 422


async def test_quote_writes_nothing(
  client: AsyncClient, session: AsyncSession, tenant: Tenant
) -> None:
  assert (await _quote(client)).status_code == 200

  for model in (Message, MessageEvent, LedgerEntry):
    assert await session.scalar(select(func.count()).select_from(model)) == 0
  balance = await session.scalar(select(Tenant.balance_cents).where(Tenant.id == tenant.id))
  assert balance == 10_000
