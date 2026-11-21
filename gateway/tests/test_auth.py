from uuid import uuid4

import pytest
from helpers import ADMIN_HEADERS, DEFAULT_KEY, MESSAGE, batch_items
from httpx import AsyncClient

READ_PATHS = ["/v1/messages", "/v1/ledger", "/v1/stats", "/v1/balance"]
WRITE_REQUESTS = [
  ("/v1/messages", MESSAGE),
  ("/v1/messages/batch", {"messages": batch_items(1)}),
]


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "unknown-key"}])
async def test_reads_require_a_valid_key(
  client: AsyncClient, tenant, path: str, headers: dict[str, str]
) -> None:
  response = await client.get(path, headers=headers)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


@pytest.mark.parametrize("path", READ_PATHS)
async def test_reads_reject_a_tenant_key_that_names_a_tenant(
  client: AsyncClient, tenant, path: str
) -> None:
  response = await client.get(
    path, params={"tenant_id": str(tenant.id)}, headers={"X-API-Key": DEFAULT_KEY}
  )

  assert response.status_code == 403
  assert response.json() == {"error": "FORBIDDEN"}


@pytest.mark.parametrize("path", READ_PATHS)
async def test_reads_fail_closed_on_a_wrong_admin_key(
  client: AsyncClient, tenant, path: str
) -> None:
  response = await client.get(
    path, headers={"X-API-Key": DEFAULT_KEY, "X-Admin-Key": "wrong-admin-key"}
  )

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}


@pytest.mark.parametrize("path", READ_PATHS)
async def test_admin_naming_an_unknown_tenant_is_404(
  client: AsyncClient, tenant, path: str
) -> None:
  response = await client.get(path, params={"tenant_id": str(uuid4())}, headers=ADMIN_HEADERS)

  assert response.status_code == 404
  assert response.json() == {"error": "NOT_FOUND"}


@pytest.mark.parametrize(("path", "payload"), WRITE_REQUESTS)
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "unknown-key"}])
async def test_writes_require_a_valid_key(
  client: AsyncClient, tenant, path: str, payload: dict, headers: dict[str, str]
) -> None:
  response = await client.post(path, json=payload, headers=headers)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}
