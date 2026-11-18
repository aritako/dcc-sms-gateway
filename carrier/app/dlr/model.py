from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DeliveryReceiptPayload(BaseModel):
  model_config = ConfigDict(populate_by_name=True)

  carrier_id: str
  status: Literal["DELIVRD", "UNDELIV", "EXPIRED"]
  timestamp: datetime
