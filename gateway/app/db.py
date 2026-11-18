from settings import settings
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = settings.db_url

engine = create_async_engine(DATABASE_URL)
