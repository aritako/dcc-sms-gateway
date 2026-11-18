from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  db_url : str
  redis_url : str
  carrier_url: str = "http://localhost:8001"

settings = Settings()