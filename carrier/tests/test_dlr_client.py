import asyncio
import hashlib
import hmac
import re
from datetime import UTC, datetime

import httpx
from app.dlr.client import GatewayDLRClient
from app.dlr.model import DeliveryReceiptPayload


def payload() -> DeliveryReceiptPayload:
  return DeliveryReceiptPayload(
    carrier_id="c-1", status="DELIVRD", timestamp=datetime(2026, 1, 1, tzinfo=UTC)
  )


def signature(body: bytes) -> str:
  return hmac.new(b"secret", body, hashlib.sha256).hexdigest()


async def test_receipt_verifies_against_a_real_http_receiver() -> None:
  received: list[tuple[bytes, str]] = []
  done = asyncio.Event()

  async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    raw_headers = await reader.readuntil(b"\r\n\r\n")
    length = int(re.search(rb"content-length: (\d+)", raw_headers, re.IGNORECASE).group(1))
    body = await reader.readexactly(length)
    header = re.search(rb"x-signature: ([0-9a-f]+)", raw_headers, re.IGNORECASE)
    received.append((body, header.group(1).decode()))
    writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
    await writer.drain()
    writer.close()
    done.set()

  server = await asyncio.start_server(handle, "127.0.0.1", 0)
  port = server.sockets[0].getsockname()[1]
  try:
    async with httpx.AsyncClient() as http:
      client = GatewayDLRClient(http, f"http://127.0.0.1:{port}", "secret")
      await client.deliver(payload())
    await asyncio.wait_for(done.wait(), timeout=2)
  finally:
    server.close()
    await server.wait_closed()

  (body, sent_signature) = received[0]
  assert sent_signature == signature(body)
  assert DeliveryReceiptPayload.model_validate_json(body).carrier_id == "c-1"
