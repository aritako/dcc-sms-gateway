import asyncio
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.database import dispose_engine, get_engine
from app.db.models import ApiKey, Price, Tenant
from app.security import hash_api_key

DEMO_CURRENCY = "USD"


@dataclass(frozen=True)
class DemoTenant:
    name: str
    opening_balance_cents: int
    api_key: str


# The plaintext keys are development-only and documented in the README. Only
# their SHA-256 hashes are written to the database.
DEMO_TENANTS = (
    DemoTenant("Acme Bank", 100_000, "dcc_acme_demo_key"),
    DemoTenant("ShopFast", 1_000_000, "dcc_shopfast_demo_key"),
)

DEMO_PRICES = (
    ("+880", 3),
    ("+971", 5),
)


async def seed() -> None:
    """Insert the baseline tenants, API keys and prices."""
    async with get_engine().begin() as connection:
        for demo in DEMO_TENANTS:
            await connection.execute(
                pg_insert(Tenant)
                .values(
                    name=demo.name,
                    opening_balance_cents=demo.opening_balance_cents,
                    balance_cents=demo.opening_balance_cents,
                    currency=DEMO_CURRENCY,
                )
                .on_conflict_do_nothing(index_elements=[Tenant.name])
            )
            tenant_id = (
                await connection.execute(select(Tenant.id).where(Tenant.name == demo.name))
            ).scalar_one()
            await connection.execute(
                pg_insert(ApiKey)
                .values(tenant_id=tenant_id, key_hash=hash_api_key(demo.api_key))
                .on_conflict_do_nothing(index_elements=[ApiKey.key_hash])
            )

        for prefix, price_per_segment_cents in DEMO_PRICES:
            await connection.execute(
                pg_insert(Price)
                .values(prefix=prefix, price_per_segment_cents=price_per_segment_cents)
                .on_conflict_do_update(
                    index_elements=[Price.prefix],
                    set_={
                        "price_per_segment_cents": price_per_segment_cents,
                        "updated_at": func.now(),
                    },
                )
            )


async def main() -> None:
    try:
        await seed()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
