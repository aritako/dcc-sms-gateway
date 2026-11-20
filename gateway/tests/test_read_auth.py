import pytest
from httpx import AsyncClient


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/ledger", "/v1/stats", "/v1/balance"])
async def test_reads_require_a_key(client: AsyncClient, path: str) -> None:
  response = await client.get(path)

  assert response.status_code == 401
  assert response.json() == {"error": "UNAUTHORIZED"}
