import asyncio

from sqlalchemy import text

from app.db.database import dispose_engine, get_engine


async def seed() -> None:
    """Insert baseline data.

    Intentionally a no-op placeholder: ticket #2 adds the domain models and the
    idempotent seed rows.
    """
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def main() -> None:
    try:
        await seed()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
