from pydantic_settings
from app.settings import get_settings
from .model import DeliveryReceiptPayload
import random, datetime
import hmac

class DeliveryReceiptSender():
  def __init__(self,):
    self.settings = get_settings()

  def create(self, carrier_id: str) -> DeliveryReceiptPayload:
    # 90% Delivered, 7% Undelivered, 3% Expired
    status = random.choices(
        ["DELIVRD", "UNDELIV", "EXPIRED"],
        weights=[self.settings.w_delivrd, self.settings.w_undeliv, self.settings.w_expired],
    )[0]
    return DeliveryReceiptPayload(
      carrier_id=carrier_id,
      status=status,
      timestamp=datetime.datetime.now()
    )
    

  def send(self, payload: DeliveryReceiptPayload):
    raw_request = payload.model_dump_json().encode()
    sha256_payload = hmac.new(self.settings.dlr_secret.encode(), raw_request, "sha256").hexdigest()
    header = {"Content-Type": "application/json", "X-Signature": sha256_payload}

    # Insert client sending logic

