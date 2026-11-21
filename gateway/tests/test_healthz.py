import pytest
from httpx import AsyncClient


async def test_healthz_ok(client: AsyncClient) -> None:
  response = await client.get("/healthz")

  assert response.status_code == 200
  assert response.json() == {"db": "ok", "redis": "ok"}


async def test_healthz_names_the_failing_dependency(
  client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  async def broken_redis() -> None:
    raise ConnectionError("redis is down")

  monkeypatch.setattr("app.main._ping_redis", broken_redis)

  response = await client.get("/healthz")

  assert response.status_code == 503
  assert response.json() == {"db": "ok", "redis": "error"}
