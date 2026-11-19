from pydantic import BaseModel, Field
from typing import Literal

class SubmitRequest(BaseModel):
  message_id: str
  to: str
  from_: str = Field(alias="from")
  text: str
  encoding: Literal["GSM7", "UCS2"]

class SubmitResponse(BaseModel):
  carrier_id: str
