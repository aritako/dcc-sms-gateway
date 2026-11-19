from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class SubmitRequest(BaseModel):
  model_config = ConfigDict(populate_by_name=True)

  message_id: str
  to: str
  from_: str = Field(alias="from")
  text: str
  encoding: Literal["GSM7", "UCS2"]

class SubmitResponse(BaseModel):
  carrier_id: str
