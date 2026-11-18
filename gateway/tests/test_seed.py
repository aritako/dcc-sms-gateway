from sqlalchemy import func, insert, select, update

from app.db.database import get_engine
from app.db.models import ApiKey, LedgerEntry, Message, Price, Tenant
from app.security import hash_api_key
from app.seed import DEMO_PRICES, DEMO_TENANTS, seed


async def _snapshot() -> tuple[dict[str, tuple[int, int, str]], set[str], dict[str, int]]:
    async with get_engine().connect() as connection:
        tenants = {
            name: (opening, balance, currency)
            for name, opening, balance, currency in (
                await connection.execute(
                    select(
                        Tenant.name,
                        Tenant.opening_balance_cents,
                        Tenant.balance_cents,
                        Tenant.currency,
                    )
                )
            ).all()
        }
        key_hashes = set((await connection.execute(select(ApiKey.key_hash))).scalars())
        prices = dict(
            (
                await connection.execute(
                    select(Price.prefix, Price.price_per_segment_cents)
                )
            ).all()
        )
    return tenants, key_hashes, prices


async def test_seed_is_idempotent() -> None:
    await seed()
    first = await _snapshot()
    await seed()
    second = await _snapshot()

    assert second == first
    tenants, key_hashes, prices = second
    assert tenants == {
        "Acme Bank": (100_000, 100_000, "USD"),
        "ShopFast": (1_000_000, 1_000_000, "USD"),
    }
    assert key_hashes == {hash_api_key(demo.api_key) for demo in DEMO_TENANTS}
    assert prices == dict(DEMO_PRICES)
    assert len(key_hashes) == 2


async def test_seed_does_not_reset_balances() -> None:
    await seed()
    async with get_engine().begin() as connection:
        await connection.execute(
            update(Tenant).where(Tenant.name == "Acme Bank").values(balance_cents=42)
        )

    await seed()

    async with get_engine().connect() as connection:
        balance = (
            await connection.execute(
                select(Tenant.balance_cents).where(Tenant.name == "Acme Bank")
            )
        ).scalar_one()
    assert balance == 42


async def test_seed_restores_the_configured_price() -> None:
    await seed()
    async with get_engine().begin() as connection:
        await connection.execute(
            update(Price).where(Price.prefix == "+880").values(price_per_segment_cents=99)
        )

    await seed()

    async with get_engine().connect() as connection:
        price = (
            await connection.execute(
                select(Price.price_per_segment_cents).where(Price.prefix == "+880")
            )
        ).scalar_one()
    assert price == 3


async def test_signed_ledger_explains_the_balance() -> None:
    await seed()

    async with get_engine().begin() as connection:
        tenant_id, opening = (
            await connection.execute(
                select(Tenant.id, Tenant.opening_balance_cents).where(
                    Tenant.name == "Acme Bank"
                )
            )
        ).one()
        message_id = (
            await connection.execute(
                insert(Message)
                .values(
                    tenant_id=tenant_id,
                    to_number="+8801711000001",
                    sender_id="ACME",
                    text="Your OTP is 123456",
                    encoding="GSM7",
                    segments=1,
                    price_per_segment_cents=3,
                    total_price_cents=3,
                )
                .returning(Message.id)
            )
        ).scalar_one()
        await connection.execute(
            insert(LedgerEntry).values(
                tenant_id=tenant_id,
                message_id=message_id,
                kind="CHARGE",
                amount_cents=-3,
                balance_after_cents=opening - 3,
            )
        )
        await connection.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(balance_cents=opening - 3)
        )
        await connection.execute(
            insert(LedgerEntry).values(
                tenant_id=tenant_id,
                message_id=message_id,
                kind="REFUND",
                amount_cents=3,
                balance_after_cents=opening,
            )
        )
        await connection.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(balance_cents=opening)
        )

    async with get_engine().connect() as connection:
        balance = (
            await connection.execute(
                select(Tenant.balance_cents).where(Tenant.name == "Acme Bank")
            )
        ).scalar_one()
        ledger_sum = (
            await connection.execute(
                select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
                    LedgerEntry.tenant_id == tenant_id
                )
            )
        ).scalar_one()
        entries = dict(
            (
                await connection.execute(
                    select(LedgerEntry.kind, LedgerEntry.amount_cents).where(
                        LedgerEntry.tenant_id == tenant_id
                    )
                )
            ).all()
        )

    assert balance == opening + ledger_sum
    assert entries == {"CHARGE": -3, "REFUND": 3}
