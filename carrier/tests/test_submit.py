import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app import store
from app.main import app
from app.settings import get_settings
from app.store import snapshot
import logging

def make_client() -> AsyncClient:
  return AsyncClient(transport=ASGITransport(app=app), base_url="http://carrier.test")


def submit_body(message_id: str = "m-1", **overrides: object) -> dict[str, object]:
  body: dict[str, object] = {
    "message_id": message_id,
    "to": "+15551234567",
    "from": "DCC",
    "text": "hello",
    "encoding": "GSM7",
  }
  body.update(overrides)
  return body


async def test_accepts_and_emits_one_dlr(sender) -> None:
  async with make_client() as client:
    response = await client.post("/submit", json=submit_body())

  assert response.status_code == 200
  payload = response.json()
  assert payload["carrier_id"].startswith("c-")
  assert [receipt.carrier_id for receipt in sender.scheduled] == [payload["carrier_id"]]
  assert sender.sent == []

  stats = snapshot()

  assert stats["accepted"] == 1
  assert stats["per_message_submits"] == {"m-1": 1}


@pytest.mark.parametrize(
  ("fault", "status", "code"),
  [
    ("p_400", 400, "INVALID_DESTINATION"),
    ("p_503", 503, "TEMPORARY_FAILURE"),
  ],
)
async def test_faults_use_the_error_body(fault, status, code, monkeypatch) -> None:
  monkeypatch.setattr(get_settings(), fault, 1.0)

  async with make_client() as client:
    response = await client.post("/submit", json=submit_body())

  assert response.status_code == status
  assert response.json() == {"error": code}


async def test_limit_is_shared_by_every_caller(monkeypatch) -> None:
  monkeypatch.setattr(get_settings(), "rate_limit_submit_per_sec", 5)

  async with make_client() as client:
    codes = [
      (await client.post("/submit", json=submit_body(f"m-{i}"))).status_code
      for i in range(10)
    ]

  assert codes[:5] == [200, 200, 200, 200, 200]
  assert set(codes[5:]) == {429}
  assert store.snapshot()["s429"] == len(codes[5:])


async def test_duplicate_message_id_returns_original_id(sender) -> None:
  async with make_client() as client:
    first = await client.post("/submit", json=submit_body())
    second = await client.post("/submit", json=submit_body(text="different"))

  assert first.status_code == 200
  assert second.status_code == 200
  assert second.json()["carrier_id"] == first.json()["carrier_id"]
  # Only the first submit schedules a receipt.
  assert len(sender.scheduled) == 1


async def test_timeout_still_accepts_and_schedules_a_dlr(sender, monkeypatch) -> None:
  monkeypatch.setattr(get_settings(), "p_timeout", 1.0)

  started = time.monotonic()
  async with make_client() as client:
    response = await client.post("/submit", json=submit_body())
  elapsed = time.monotonic() - started

  assert response.status_code == 200
  assert response.json()["carrier_id"].startswith("c-")
  assert elapsed >= 0.04
  assert len(sender.scheduled) == 1
  assert store.snapshot()["timeouts"] == 1


async def test_a_retry_during_the_hang_gets_the_original_id(sender, monkeypatch) -> None:
  monkeypatch.setattr(get_settings(), "p_timeout", 1.0)
  monkeypatch.setattr(get_settings(), "hang_seconds", 0.2)

  async with make_client() as client:
    # The first submit is accepted but still holding its response open.
    first = asyncio.create_task(client.post("/submit", json=submit_body()))
    await asyncio.sleep(0.1)
    retry = await client.post("/submit", json=submit_body())
    delayed = await first

  assert retry.status_code == 200
  assert retry.json() == {"carrier_id": delayed.json()["carrier_id"]}
  # The acceptance and its DLR are not repeated.
  assert len(sender.scheduled) == 1
  assert store.snapshot()["per_message_submits"] == {"m-1": 2}


async def test_early_dlr_is_sent_before_the_response(sender, monkeypatch) -> None:
  monkeypatch.setattr(get_settings(), "p_early_dlr", 1.0)

  async with make_client() as client:
    response = await client.post("/submit", json=submit_body())

    # send() is awaited by the handler, so it has already happened while the
    # caller is still waiting for the response.
    assert len(sender.sent) == 1

  assert response.status_code == 200
  assert sender.scheduled == []
