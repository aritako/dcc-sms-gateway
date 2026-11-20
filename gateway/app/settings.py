from functools import lru_cache
from typing import Any

from pydantic import ValidationError, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  """Runtime configuration, read from the environment (or a local .env)."""

  model_config = SettingsConfigDict(
    env_file=(".env", "../.env"),
    env_file_encoding="utf-8",
    extra="ignore",
  )

  database_url: str
  redis_url: str
  dlr_secret: str
  admin_api_key: str
  carrier_url: str = "http://localhost:8001"

  # WORKER CONFIGS
  # Submits per second across ALL workers. The carrier rejects above 50/s, so 50
  # is a hard ceiling; the 45 default leaves headroom for clock and network jitter.
  carrier_submit_rate: int = Field(default=45, ge=1, le=50)
  carrier_timeout_seconds: float = 2.0

  worker_lease_seconds: float = 30.0
  worker_claim_size: int = 20
  worker_retry_seconds: float = 5.0
  worker_idle_seconds: float = 0.25


def load_settings(**kwargs: Any) -> Settings:
  """Build Settings, turning a missing variable into an actionable error."""
  try:
    return Settings(**kwargs)
  except ValidationError as exc:
    missing = sorted(
      str(error["loc"][0]).upper() for error in exc.errors() if error["type"] == "missing"
    )
    if missing:
      raise RuntimeError(
        "Missing required configuration: "
        + ", ".join(missing)
        + ". Copy .env.example to .env and fill in the values."
      ) from exc
    raise RuntimeError(f"Invalid configuration: {exc}") from exc


@lru_cache
def get_settings() -> Settings:
  """Return cached settings."""
  return load_settings()
