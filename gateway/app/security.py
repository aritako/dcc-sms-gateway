import hashlib


def hash_api_key(key: str) -> str:
  """Hash an API key for storage.

  Keys are high-entropy random tokens, so a fast SHA-256 digest is safe and
  keeps authentication a single indexed lookup. Never store the plaintext.
  """
  return hashlib.sha256(key.encode("utf-8")).hexdigest()
