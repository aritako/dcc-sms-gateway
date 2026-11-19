import uuid

from pydantic import BaseModel, Field

from app.db.models import Encoding


class MessagesRequest(BaseModel):
  to: str
  from_: str = Field(alias="from")
  text: str
  client_ref: str | None = None


class MessagesResponse(BaseModel):
  message_id: str


class QuoteRequest(BaseModel):
  # ASCII digits only: Python's \d would also accept other scripts' numerals.
  to: str = Field(pattern=r"^\+[1-9][0-9]{1,14}$")
  text: str = Field(min_length=1)
  # Only used with X-Admin-Key; a tenant key already identifies its tenant.
  tenant_id: uuid.UUID | None = None


class QuoteResponse(BaseModel):
  encoding: Encoding
  segments: int
  # Integer cents, like every other money field in the database.
  price_per_segment: int
  total_price: int
  currency: str
