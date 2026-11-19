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
    # Default back to utf-16-le, since thats what SMS usually uses.
    # Not specified in the spec but I'm going with this
    return len(text.encode("utf-16-le")) // 2

  def count_segments(self, text: str) -> tuple[Encoding, int]:
    if not text:
      raise ValueError("text must not be empty")
    encoding = self.detect_encoding(text)
    length = self.message_length(text, encoding)
    single, multi = SEGMENT_LIMITS[encoding]
    segments = 1 if length <= single else math.ceil(length / multi)
    return encoding, segments
