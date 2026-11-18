import asyncio
import logging

async def main():
  logging.basicConfig(level=logging.INFO)
  while True:
    logging.info("worker alive")
    await asyncio.sleep(5)

if __name__ == "__main__":
  asyncio.run(main())