from functools import lru_cache
from typing import Any

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  """Runtime configuration, read from the environment (or a local .env)."""

  model_config = SettingsConfigDict(
    env_file=(".env", "../.env"),
    env_file_encoding="utf-8",
    extra="ignore",
  )

  dlr_secret: str = "dev-dlr-secret"
  gateway_url: str = "http://localhost:8000"

  rate_limit_submit_per_sec: int = 50
  latency_min: float = 0.02
  latency_max: float = 0.3

  p_503: float = 0.03
  p_400: float = 0.01
  p_timeout: float = 0.01
  hang_seconds: float = 5

  dlr_min_s: float = 0.5
  dlr_max_s: float = 10
  p_dup_dlr: float = 0.10
  p_early_dlr: float = 0.05
  w_delivrd: float = 0.90
  w_undeliv: float = 0.07
  w_expired: float = 0.03

  seed: int | None = Field(default=None, validation_alias="CARRIER_SEED")


def load_settings(**kwargs: Any) -> Settings:
  """Build Settings. Throws actionable errors."""
  try:
    return Settings(**kwargs)
  except ValidationError as exc:
    missing = sorted(
      str(error["loc"][0]).upper()
      for error in exc.errors()
      if error["type"] == "missing"
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
