import pytest
from helpers import (
  ADMIN_HEADERS,
  DEFAULT_KEY,
  assert_nothing_written,
  create_tenant,
  ensure_price,
)
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

QUOTE_PATH = "/v1/messages/quote"
BANGLADESH = "+8801712345678"


@pytest.fixture
async def tenant(session: AsyncSession):
  return await create_tenant(
    session,
    name="Acme",
    prices=(("+880", 3), ("+971", 5)),
  )


async def _quote(
  client: AsyncClient,
  *,
  to: str = BANGLADESH,
  text: str = "hello",
  headers: dict[str, str | bytes] | None = None,
  **fields: object,
) -> Response:
  return await client.post(
    QUOTE_PATH,
    json={"to": to, "text": text, **fields},
    headers={"x-api-key": DEFAULT_KEY} if headers is None else headers,
  )


async def test_quote_reports_encoding_segments_and_integer_cents(
  client: AsyncClient, tenant
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


@pytest.mark.parametrize(
  ("text", "encoding", "segments"),
  [("a" * 161, "GSM7", 2), ("😀" * 36, "UCS2", 2)],
)
async def test_segments_multiply_the_price(
  client: AsyncClient, tenant, text: str, encoding: str, segments: int
) -> None:
  response = await _quote(client, text=text)

  body = response.json()
  assert body["encoding"] == encoding
  assert body["segments"] == segments
  assert body["total_price"] == 3 * segments


async def test_price_uses_the_longest_matching_prefix(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  await ensure_price(session, "+8801", 9)
  await session.commit()

  assert (await _quote(client, to="+8801712345678")).json()["total_price"] == 9
  assert (await _quote(client, to="+8809123456789")).json()["total_price"] == 3


async def test_price_matches_a_different_prefix(client: AsyncClient, tenant) -> None:
  response = await _quote(client, to="+971501234567")

  assert response.json()["price_per_segment"] == 5
  assert response.json()["total_price"] == 5


async def test_currency_comes_from_the_tenant(
  client: AsyncClient, session: AsyncSession, tenant
) -> None:
  await create_tenant(session, name="Euro Bank", currency="EUR", api_key="eur-key")

  response = await _quote(client, headers={"x-api-key": "eur-key"})

  assert response.json()["currency"] == "EUR"


async def test_admin_key_quotes_for_the_named_tenant(client: AsyncClient, tenant) -> None:
  response = await _quote(client, headers=ADMIN_HEADERS, tenant_id=str(tenant.id))

  assert response.status_code == 200
  assert response.json()["currency"] == "USD"


async def test_admin_key_requires_a_tenant_id(client: AsyncClient, tenant) -> None:
  response = await _quote(client, headers=ADMIN_HEADERS)

  assert response.status_code == 422
  assert response.json() == {"error": "TENANT_ID_REQUIRED"}


async def test_admin_key_rejects_an_unknown_tenant(client: AsyncClient, tenant) -> None:
  response = await _quote(
    client,
    headers=ADMIN_HEADERS,
    tenant_id="00000000-0000-0000-0000-000000000000",
  )

  assert response.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "nope"}])
async def test_quote_requires_a_valid_key(
  client: AsyncClient, tenant, headers: dict[str, str]
) -> None:
  response = await _quote(client, headers=headers)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


async def test_wrong_admin_key_is_unauthorized(client: AsyncClient, tenant) -> None:
  response = await _quote(client, headers={"x-admin-key": "wrong"}, tenant_id=str(tenant.id))

  assert response.status_code == 401


async def test_non_ascii_admin_key_is_unauthorized(client: AsyncClient, tenant) -> None:
  # Latin-1 bytes reach the app as "café"; compare_digest on str would raise.
  response = await _quote(client, headers={"x-admin-key": b"caf\xe9"}, tenant_id=str(tenant.id))

  assert response.status_code == 401


@pytest.mark.parametrize(
  ("to", "text", "error"),
  [
    ("+33123456789", "hi", "UNSUPPORTED_DESTINATION"),
    ("08801712345678", "hi", None),
    (BANGLADESH, "", None),
  ],
)
async def test_quote_rejects_invalid_input_and_writes_nothing(
  client: AsyncClient,
  session: AsyncSession,
  tenant,
  to: str,
  text: str,
  error: str | None,
) -> None:
  response = await _quote(client, to=to, text=text)

  assert response.status_code == 422
  if error is not None:
    assert response.json() == {"error": error}
  else:
    assert "detail" in response.json()
  await assert_nothing_written(session, tenant, 10_000)
