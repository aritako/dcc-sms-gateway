from datetime import UTC, datetime

import pytest

from app import store
from app.dlr.model import DeliveryReceiptPayload
from app.main import app
from app.rate_limit import limiter
from app.settings import get_settings


class FakeSender:
  """Records receipts instead of POSTing them, so submit tests stay fast."""

  def __init__(self) -> None:
    self.scheduled: list[DeliveryReceiptPayload] = []
    self.sent: list[DeliveryReceiptPayload] = []

  def create(self, carrier_id: str) -> DeliveryReceiptPayload:
    return DeliveryReceiptPayload(
      carrier_id=carrier_id,
      status="DELIVRD",
      timestamp=datetime.now(UTC),
    )

  def schedule(self, payload: DeliveryReceiptPayload, delay: float | None = None) -> None:
    self.scheduled.append(payload)

  async def send(self, payload: DeliveryReceiptPayload) -> None:
    self.sent.append(payload)


@pytest.fixture(autouse=True)
def sender(monkeypatch: pytest.MonkeyPatch) -> FakeSender:
  settings = get_settings()
  for name, value in {
    "latency_min": 0.0,
    "latency_max": 0.0,
    "p_503": 0.0,
    "p_400": 0.0,
    "p_timeout": 0.0,
    "p_early_dlr": 0.0,
    "hang_seconds": 0.05,
    "dedupe_message_id": True,
    "rate_limit_submit_per_sec": 50,
    "seed": None,
  }.items():
    monkeypatch.setattr(settings, name, value)

  limiter.reset()
  store.reset()
  app.state.dlr_sender = FakeSender()
  yield app.state.dlr_sender
  store.reset()
