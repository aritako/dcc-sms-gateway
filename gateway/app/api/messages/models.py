
from pydantic import BaseModel, Field


class MessagesRequest(BaseModel):
  to: str
  from_: str = Field(alias="from")
  text: str
  client_ref: str | None = None

class MessagesResponse(BaseModel):
  message_id: str
  