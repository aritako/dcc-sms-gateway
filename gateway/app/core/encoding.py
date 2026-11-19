import math

from app.core import GSM7_BASIC, GSM7_EXTENSION, SEGMENT_LIMITS
from app.db.models import Encoding


class EncodingService:
  def detect_encoding(self, text: str) -> Encoding:
    if all(c in GSM7_BASIC or c in GSM7_EXTENSION for c in text):
      return Encoding.GSM7
    return Encoding.UCS2

  def message_length(self, text: str, encoding: Encoding) -> int:
    if encoding is Encoding.GSM7:
      return sum(2 if c in GSM7_EXTENSION else 1 for c in text)
    # UCS-2 is measured in UTF-16 code units: astral characters count as two,
    # and a lone surrogate counts as one instead of raising on encode().
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)

  def count_segments(self, text: str) -> tuple[Encoding, int]:
    if not text:
      raise ValueError("text must not be empty")
    encoding = self.detect_encoding(text)
    length = self.message_length(text, encoding)
    single, multi = SEGMENT_LIMITS[encoding]
    segments = 1 if length <= single else math.ceil(length / multi)
    return encoding, segments
