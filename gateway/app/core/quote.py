from dataclasses import dataclass

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encoding import EncodingService
from app.db.models import Encoding, Price


class UnknownDestination(Exception):
  """No `prices` row covers the destination's prefix."""


@dataclass(frozen=True, slots=True)
class Quote:
  """Everything a quote reports. Computing one writes nothing."""

  encoding: Encoding
  segments: int
  price_per_segment_cents: int
  total_price_cents: int
  currency: str


class QuoteService:
  def __init__(self, db: AsyncSession, encoding: EncodingService, currency: str) -> None:
    self.db = db
    self.encoding = encoding
    self.currency = currency

  async def quote(self, text: str, to: str) -> Quote:
    """Price `text` for `to` in the tenant's currency.

    `to` must already be validated E.164 so it cannot carry LIKE wildcards.
    """
    encoding, segments = self.encoding.count_segments(text)

    # Longest match wins: +8801 beats +880 for +8801712345678.
    price_per_segment_cents = await self.db.scalar(
      select(Price.price_per_segment_cents)
      .where(literal(to).like(Price.prefix + "%"))
      .order_by(func.length(Price.prefix).desc())
      .limit(1)
    )
    if price_per_segment_cents is None:
      raise UnknownDestination(to)

    return Quote(
      encoding=encoding,
      segments=segments,
      price_per_segment_cents=price_per_segment_cents,
      total_price_cents=segments * price_per_segment_cents,
      currency=self.currency,
    )
