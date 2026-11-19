from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encoding import EncodingService
from app.core.quote import QuoteService
from app.db.database import get_session
from app.db.models import Price

GSM7_MULTI_SEGMENT_TEXT = "a" * 161  # 161 septets -> 2 segments of 153


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
  """Use the same generator the FastAPI dependency hands to a request."""
  async for session in get_session():
    yield session


@pytest.fixture
async def quote_service(session: AsyncSession) -> QuoteService:
  session.add(Price(prefix="+880", price_per_segment_cents=3))
  await session.commit()
  return QuoteService(session, EncodingService())


async def test_single_segment(quote_service: QuoteService) -> None:
  assert await quote_service.quote("hello", "+880") == 3


async def test_multi_segment_multiplies_price(quote_service: QuoteService) -> None:
  assert await quote_service.quote(GSM7_MULTI_SEGMENT_TEXT, "+880") == 6


async def test_ucs2_text_is_priced_by_segment(quote_service: QuoteService) -> None:
  # 71 UTF-16 code units exceeds the 70-unit single UCS2 segment.
  assert await quote_service.quote("漢" * 71, "+880") == 6


async def test_unknown_prefix_raises(quote_service: QuoteService) -> None:
  with pytest.raises(LookupError):
    await quote_service.quote("hello", "+999")
