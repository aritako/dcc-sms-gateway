from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DeliveryReceiptPayload(BaseModel):
  carrier_id: str
  status: Literal["DELIVRD", "UNDELIV", "EXPIRED"]
  timestamp: datetime
