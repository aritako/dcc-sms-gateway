from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encoding import EncodingService
from app.db.models import Price


class QuoteService:
  def __init__(self, db: AsyncSession, encoding: EncodingService) -> None:
    self.db = db
    self.encoding = encoding

  async def quote(self, text: str, to: str) -> int:
    """Price of sending `text` -> `to`, in cents."""
    _, segments = self.encoding.count_segments(text)

    price_per_segment_cents = await self.db.scalar(
      select(Price.price_per_segment_cents)
      .where(literal(to).like(Price.prefix + "%"))
      .order_by(func.length(Price.prefix).desc())
      .limit(1)
    )
    if price_per_segment_cents is None:
      raise LookupError("No price is configured for the destination prefix.")
    return segments * price_per_segment_cents
