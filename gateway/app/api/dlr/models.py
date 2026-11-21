from pydantic import AwareDatetime, BaseModel, Field


class DlrPayload(BaseModel):
  carrier_id: str = Field(min_length=1)
  status: str
  timestamp: AwareDatetime
