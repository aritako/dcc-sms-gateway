import httpx
from app.dlr.client import GatewayDLRClient
from app.dlr.handler import DeliveryReceiptSender
from app.settings import Settings


def make_sender(transport: httpx.MockTransport, **overrides: object) -> DeliveryReceiptSender:
  http = httpx.AsyncClient(transport=transport)
  client = GatewayDLRClient(http, "http://gateway.test", "secret")
  return DeliveryReceiptSender(client, Settings(**overrides))


async def test_status_mix_honours_forced_weights() -> None:
  sender = make_sender(
    httpx.MockTransport(lambda request: httpx.Response(200)),
    w_delivrd=1.0,
    w_undeliv=0.0,
    w_expired=0.0,
  )
  receipt = sender.create("c-1")
  assert receipt.status == "DELIVRD"
  assert receipt.timestamp.tzinfo is not None


async def test_duplicate_rate_resends_identical_bytes() -> None:
  seen: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    return httpx.Response(200)

  sender = make_sender(
    httpx.MockTransport(handler), p_dup_dlr=1.0, dlr_min_s=0.0, dlr_max_s=0.0
  )
  await sender.send_after(sender.create("c-1"), 0)

  assert len(seen) == 2
  assert seen[0].content == seen[1].content
  assert seen[0].headers["x-signature"] == seen[1].headers["x-signature"]
