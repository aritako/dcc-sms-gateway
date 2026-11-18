from app.settings import settings
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = settings.database_url

engine = create_async_engine(DATABASE_URL)
