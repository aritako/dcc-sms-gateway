from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  database_url : str
  redis_url : str
  carrier_url: str = "http://localhost:8001"

settings = Settings()