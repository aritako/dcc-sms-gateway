from app.db.models import Encoding as Encoding

# GSM 03.38 basic table
GSM7_BASIC = set(
  "@£$¥èéùìòÇ\nØø\rÅå"
  "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
  " !\"#¤%&'()*+,-./"
  "0123456789:;<=>?"
  "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
  "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# Extension table: still GSM-7, but each costs 2 septets (ESC + char).
GSM7_EXTENSION = set("\f^{}\\[~]|€")

SEGMENT_LIMITS = {Encoding.GSM7: (160, 153), Encoding.UCS2: (70, 67)}
