import hashlib
import hmac

import httpx

from .model import DeliveryReceiptPayload


class GatewayDLRClient:
  """Delivers signed delivery receipts to the gateway's /carrier/dlr endpoint.

  The signature covers the bytes actually put on the wire, so the payload is
  serialized exactly once and sent as ``content``: httpx must never get the
  chance to re-serialize it.
  """

  def __init__(self, http: httpx.AsyncClient, base_url: str, secret: str) -> None:
    self._http = http
    self._base_url = base_url.rstrip("/")
    self._secret = secret.encode()

  def sign(self, body: bytes) -> str:
    """Hex HMAC-SHA256 of ``body``, keyed with the shared DLR secret."""
    return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

  def encode(self, payload: DeliveryReceiptPayload) -> tuple[bytes, dict[str, str]]:
    """Serialize ``payload`` once and return the body with its headers."""
    body = payload.model_dump_json().encode("utf-8")
    return body, {"Content-Type": "application/json", "X-Signature": self.sign(body)}

  async def deliver(self, payload: DeliveryReceiptPayload) -> httpx.Response:
    """POST ``payload`` to the gateway; raise on a non-2xx response."""
    body, headers = self.encode(payload)
    response = await self._http.post(
      f"{self._base_url}/carrier/dlr",
      content=body,
      headers=headers,
    )
    response.raise_for_status()
    return response
