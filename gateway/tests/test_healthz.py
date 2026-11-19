import pytest
from httpx import AsyncClient


async def test_root(client: AsyncClient) -> None:
  response = await client.get("/")

  assert response.status_code == 200
  assert response.json() == {"message": "Hello World"}


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


async def test_fake_carrier_records_submits(fake_carrier_client: AsyncClient, fake_carrier) -> None:
  payload = {"message_id": "msg-1", "to": "+15550001", "body": "hi"}

  response = await fake_carrier_client.post("/submit", json=payload)

  assert response.status_code == 202
  assert response.json() == {"carrier_id": "fake-1"}
  assert fake_carrier.submits == [payload]
