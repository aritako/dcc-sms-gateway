import uuid

from pydantic import BaseModel, Field

from app.db.models import Encoding, MessageStatus

# ASCII digits only, for phone verification
E164_PATTERN = r"^\+[1-9][0-9]{1,14}$"

# Max 11 alphanum chars
SENDER_ID_PATTERN = r"^[A-Za-z0-9]{1,11}$"


class MessagesRequest(BaseModel):
  to: str = Field(pattern=E164_PATTERN)
  from_: str = Field(alias="from", pattern=SENDER_ID_PATTERN)
  text: str = Field(min_length=1)
  client_ref: str | None = None


class MessagesResponse(BaseModel):
  id: uuid.UUID
  status: MessageStatus
  client_ref: str | None
  encoding: Encoding
  segments: int
  price_per_segment: int
  total_price: int
  currency: str


class QuoteRequest(BaseModel):
  to: str = Field(pattern=E164_PATTERN)
  text: str = Field(min_length=1)
  tenant_id: uuid.UUID | None = None


class QuoteResponse(BaseModel):
  encoding: Encoding
  segments: int
  # Integer cents
  price_per_segment: int
  total_price: int
  currency: str
