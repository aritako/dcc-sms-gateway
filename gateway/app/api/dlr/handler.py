import hashlib
import hmac


def signature_matches(secret: str, body: bytes, signature: str | None) -> bool:
  """Hex HMAC-SHA256 over the exact request bytes, compared in constant time."""
  if signature is None:
    return False
  expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
  try:
    return hmac.compare_digest(expected, signature)
  except TypeError:
    # compare_digest rejects non-ASCII str; that can only be a bad signature.
    return False
