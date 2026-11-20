import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Encoding, MessageStatus

# ASCII digits only, for phone verification
E164_PATTERN = r"^\+[1-9][0-9]{1,14}$"

# Max 11 alphanum chars
SENDER_ID_PATTERN = r"^[A-Za-z0-9]{1,11}$"

MAX_BATCH_SIZE = 1_000


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


class BatchRequest(BaseModel):
  messages: list[dict[str, Any]]


class BatchMessage(BaseModel):
  id: uuid.UUID
  status: MessageStatus
  client_ref: str | None
  encoding: Encoding
  segments: int
  price_per_segment: int
  total_price: int


class BatchResponse(BaseModel):
  batch_id: uuid.UUID
  messages: list[BatchMessage]
  total_price: int
  currency: str


class MessageSummary(BaseModel):
  """A message row as it appears in lists. `text` is only on the detail."""

  # Money fields are integer cents, like the send and balance responses.
  model_config = ConfigDict(populate_by_name=True)

  id: uuid.UUID
  tenant_id: uuid.UUID
  tenant_name: str
  to: str
  from_: str = Field(alias="from")
  status: MessageStatus
  segments: int
  price_per_segment: int
  total_price: int
  currency: str
  client_ref: str | None
  batch_id: uuid.UUID | None
  created_at: datetime


class MessageEventOut(BaseModel):
  id: uuid.UUID
  status: MessageStatus
  created_at: datetime


class MessageDetail(MessageSummary):
  text: str
  encoding: Encoding
  carrier: str
  carrier_id: str | None
  updated_at: datetime
  events: list[MessageEventOut]


class MessagesPage(BaseModel):
  messages: list[MessageSummary]
  limit: int
  offset: int
  has_more: bool
