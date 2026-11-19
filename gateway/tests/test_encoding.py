import pytest

from app.core import GSM7_BASIC, GSM7_EXTENSION
from app.core.encoding import EncodingService
from app.db.models import Encoding

service = EncodingService()


def test_table_is_the_full_gsm_03_38_alphabet() -> None:
  # 128 septet values, minus 0x1B which is the escape into the extension table.
  assert len(GSM7_BASIC) == 127
  assert len(GSM7_EXTENSION) == 10
  assert len(GSM7_BASIC | GSM7_EXTENSION) == 137
  assert "\x1b" not in GSM7_BASIC

  for char in "@£$¥¤¡§¿ÄÖÑÜäöñüàΔΦΓΛΩΠΨΣΘΞ":
    assert char in GSM7_BASIC


def test_extension_table_is_the_shifted_characters() -> None:
  assert GSM7_EXTENSION == frozenset({"{", "}", "[", "]", "~", "\\", "^", "|", "€", "\f"})


@pytest.mark.parametrize("char", sorted(GSM7_EXTENSION))
def test_every_extension_character_stays_gsm7_and_costs_two_septets(char: str) -> None:
  assert service.detect_encoding(char) is Encoding.GSM7
  assert service.message_length(char, Encoding.GSM7) == 2
  assert service.count_segments(char) == (Encoding.GSM7, 1)


def test_single_segment_boundary_is_160_septets() -> None:
  assert service.count_segments("a" * 160) == (Encoding.GSM7, 1)
  assert service.count_segments("a" * 161) == (Encoding.GSM7, 2)


def test_multi_segment_capacity_is_153_septets() -> None:
  assert service.count_segments("a" * 306) == (Encoding.GSM7, 2)
  assert service.count_segments("a" * 307) == (Encoding.GSM7, 3)


def test_euro_costs_two_septets_so_the_boundary_is_eighty() -> None:
  assert service.count_segments("€" * 80) == (Encoding.GSM7, 1)
  assert service.message_length("€" * 80, Encoding.GSM7) == 160
  assert service.count_segments("€" * 81) == (Encoding.GSM7, 2)


def test_extension_character_is_not_a_single_character() -> None:
  assert service.count_segments("a" * 159 + "€") == (Encoding.GSM7, 2)


def test_characters_outside_the_table_force_ucs2() -> None:
  assert service.detect_encoding("আপনার অর্ডার") is Encoding.UCS2
  assert service.detect_encoding("a" * 70 + "আ") is Encoding.UCS2


def test_ucs2_boundary_is_70_utf16_units() -> None:
  assert service.count_segments("আ" * 70) == (Encoding.UCS2, 1)
  assert service.count_segments("আ" * 71) == (Encoding.UCS2, 2)
  assert service.count_segments("আ" * 134) == (Encoding.UCS2, 2)
  assert service.count_segments("আ" * 135) == (Encoding.UCS2, 3)


def test_emoji_counts_as_two_utf16_units() -> None:
  assert service.message_length("😀", Encoding.UCS2) == 2
  assert service.count_segments("😀" * 35) == (Encoding.UCS2, 1)
  assert service.count_segments("😀" * 36) == (Encoding.UCS2, 2)


def test_empty_text_is_rejected() -> None:
  with pytest.raises(ValueError, match="must not be empty"):
    service.count_segments("")
