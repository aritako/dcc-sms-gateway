from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import datetime

class DeliveryReceiptPayload(BaseModel):
  carrier_id: str
  status: Literal["DELIVRD", "UNDELIV", "EXPIRED"]
  timestamp: datetime