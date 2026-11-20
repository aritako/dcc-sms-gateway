from typing import Literal

from pydantic import BaseModel, Field


class SubmitRequest(BaseModel):
  message_id: str
  to: str
  from_: str = Field(alias="from")
  text: str
  encoding: Literal["GSM7", "UCS2"]

class SubmitResponse(BaseModel):
  carrier_id: str
