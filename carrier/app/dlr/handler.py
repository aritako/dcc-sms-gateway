import asyncio
import logging
import random
from datetime import UTC, datetime

import httpx

from app.settings import Settings, get_settings
from .client import GatewayDLRClient
from .model import DeliveryReceiptPayload

logger = logging.getLogger(__name__)


class DeliveryReceiptSender:
  def __init__(self, client: GatewayDLRClient, settings: Settings | None = None) -> None:
    self._client = client
    self._settings = settings or get_settings()
    self._tasks: set[asyncio.Task[None]] = set()

  def create(self, carrier_id: str) -> DeliveryReceiptPayload:
    # Default: 90% Delivered, 7% Undelivered, 3% Expired
    status = random.choices(
      ["DELIVRD", "UNDELIV", "EXPIRED"],
      weights=[
        self._settings.w_delivrd,
        self._settings.w_undeliv,
        self._settings.w_expired,
      ],
    )[0]
    return DeliveryReceiptPayload(
      carrier_id=carrier_id,
      status=status,
      timestamp=datetime.now(UTC),
    )

  async def send(self, payload: DeliveryReceiptPayload) -> None:
    try:
      await self._client.deliver(payload)
    except httpx.HTTPError:
      logger.warning("delivery receipt failed for carrier_id=%s", payload.carrier_id)

  async def send_after(self, payload: DeliveryReceiptPayload, delay: float) -> None:
    if delay > 0:
      await asyncio.sleep(delay)
    await self.send(payload)
    # Duplicates reuse the same payload, so body and signature are identical.
    if random.random() < self._settings.p_dup_dlr:
      await self.send(payload)

  def schedule(self, payload: DeliveryReceiptPayload, delay: float | None = None) -> None:
    # Either delay sending from 0.5s to 10s (default), or use the caller's override.
    if delay is None:
      delay = random.uniform(self._settings.dlr_min_s, self._settings.dlr_max_s)
    task = asyncio.create_task(
      self.send_after(payload, delay)
    )
    self._tasks.add(task)
    task.add_done_callback(self._tasks.discard)

  async def aclose(self) -> None:
    """Cancel in-flight receipts so shutdown does not wait on their delays."""
    for task in list(self._tasks):
      task.cancel()
    if self._tasks:
      await asyncio.gather(*self._tasks, return_exceptions=True)
