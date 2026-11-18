import hashlib
import hmac
from datetime import UTC, datetime

import httpx
import pytest

from app.dlr.client import GatewayDLRClient
from app.dlr.model import DeliveryReceiptPayload


def payload() -> DeliveryReceiptPayload:
  return DeliveryReceiptPayload(
    carrier_id="c-1", status="DELIVRD", timestamp=datetime(2026, 1, 1, tzinfo=UTC)
  )


def signature(body: bytes) -> str:
  return hmac.new(b"secret", body, hashlib.sha256).hexdigest()


async def test_signs_the_exact_bytes_sent() -> None:
  seen: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    return httpx.Response(200)

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
    client = GatewayDLRClient(http, "http://gateway.test/", "secret")
    await client.deliver(payload())

  (request,) = seen
  assert request.url == "http://gateway.test/carrier/dlr"
  assert request.headers["content-type"] == "application/json"
  assert request.headers["x-signature"] == signature(request.content)


async def test_duplicate_delivery_reuses_identical_body_and_signature() -> None:
  seen: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    return httpx.Response(200)

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
    client = GatewayDLRClient(http, "http://gateway.test", "secret")
    receipt = payload()
    await client.deliver(receipt)
    await client.deliver(receipt)

  first, second = seen
  assert first.content == second.content
  assert first.headers["x-signature"] == second.headers["x-signature"]


async def test_non_2xx_raises() -> None:
  transport = httpx.MockTransport(lambda request: httpx.Response(500))

  async with httpx.AsyncClient(transport=transport) as http:
    client = GatewayDLRClient(http, "http://gateway.test", "secret")
    with pytest.raises(httpx.HTTPStatusError):
      await client.deliver(payload())
