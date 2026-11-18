import asyncio
from app.db.database import engine

async def main():
  # placeholder
  await engine.dispose()

if __name__ == "__main__":
  asyncio.run(main())