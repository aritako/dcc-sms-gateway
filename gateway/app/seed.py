import asyncio
from .db import engine

async def main():
  # placeholder
  await engine.dispose()

if __name__ == "__main__":
  asyncio.run(main())