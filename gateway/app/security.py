import hashlib
import secrets

from app.settings import get_settings


def hash_api_key(key: str) -> str:
  """Hash an API key for storage.

  Keys are high-entropy random tokens, so a fast SHA-256 digest is safe and
  keeps authentication a single indexed lookup. Never store the plaintext.
  """
  return hashlib.sha256(key.encode("utf-8")).hexdigest()


def admin_key_matches(candidate: str) -> bool:
  """Constant-time compare of `candidate` with the configured admin key."""
  # compare_digest raises TypeError on non-ASCII str; bytes compare is total.
  return secrets.compare_digest(
    candidate.encode("utf-8"), get_settings().admin_api_key.encode("utf-8")
  )
