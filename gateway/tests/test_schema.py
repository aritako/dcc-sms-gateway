import uuid

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.exc import IntegrityError

from app.db.database import get_engine
from app.db.models import ApiKey, LedgerEntry, Message, MessageEvent, Tenant
from app.security import hash_api_key


async def _insert_tenant(name: str = "Test Tenant", balance: int = 1000) -> uuid.UUID:
    async with get_engine().begin() as connection:
        result = await connection.execute(
            insert(Tenant)
            .values(
                name=name,
                opening_balance_cents=balance,
                balance_cents=balance,
            )
            .returning(Tenant.id)
        )
        return result.scalar_one()


async def _insert_message(tenant_id: uuid.UUID, **overrides: object) -> uuid.UUID:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "to_number": "+8801711000001",
        "sender_id": "ACME",
        "text": "Your OTP is 123456",
        "encoding": "GSM7",
        "segments": 1,
        "price_per_segment_cents": 3,
        "total_price_cents": 3,
    }
    values.update(overrides)
    async with get_engine().begin() as connection:
        result = await connection.execute(insert(Message).values(**values).returning(Message.id))
        return result.scalar_one()


async def _insert_event(message_id: uuid.UUID, status: str = "ACCEPTED") -> uuid.UUID:
    async with get_engine().begin() as connection:
        result = await connection.execute(
            insert(MessageEvent)
            .values(message_id=message_id, status=status)
            .returning(MessageEvent.id)
        )
        return result.scalar_one()


async def _insert_ledger(
    tenant_id: uuid.UUID,
    message_id: uuid.UUID | None,
    kind: str,
    amount_cents: int,
    balance_after_cents: int,
) -> uuid.UUID:
    async with get_engine().begin() as connection:
        result = await connection.execute(
            insert(LedgerEntry)
            .values(
                tenant_id=tenant_id,
                message_id=message_id,
                kind=kind,
                amount_cents=amount_cents,
                balance_after_cents=balance_after_cents,
            )
            .returning(LedgerEntry.id)
        )
        return result.scalar_one()


async def test_negative_balance_is_rejected() -> None:
    tenant_id = await _insert_tenant()

    with pytest.raises(IntegrityError):
        async with get_engine().begin() as connection:
            await connection.execute(
                update(Tenant)
                .where(Tenant.id == tenant_id)
                .values(balance_cents=-1)
            )


async def test_negative_opening_balance_is_rejected() -> None:
    with pytest.raises(IntegrityError):
        async with get_engine().begin() as connection:
            await connection.execute(
                insert(Tenant).values(
                    name="Bad Tenant",
                    opening_balance_cents=-1,
                    balance_cents=0,
                )
            )


async def test_duplicate_api_key_hash_is_rejected() -> None:
    tenant_id = await _insert_tenant()
    key_hash = hash_api_key("same-key")

    async with get_engine().begin() as connection:
        await connection.execute(insert(ApiKey).values(tenant_id=tenant_id, key_hash=key_hash))

    with pytest.raises(IntegrityError):
        async with get_engine().begin() as connection:
            await connection.execute(
                insert(ApiKey).values(tenant_id=tenant_id, key_hash=key_hash)
            )


async def test_duplicate_charge_and_refund_are_rejected() -> None:
    tenant_id = await _insert_tenant()
    message_id = await _insert_message(tenant_id)

    await _insert_ledger(tenant_id, message_id, "CHARGE", -3, 997)
    await _insert_ledger(tenant_id, message_id, "REFUND", 3, 1000)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "CHARGE", -3, 997)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "REFUND", 3, 1000)


async def test_duplicate_message_status_event_is_rejected() -> None:
    tenant_id = await _insert_tenant()
    message_id = await _insert_message(tenant_id)

    await _insert_event(message_id, "ACCEPTED")

    with pytest.raises(IntegrityError):
        await _insert_event(message_id, "ACCEPTED")


async def test_carrier_id_is_unique_only_when_set() -> None:
    tenant_id = await _insert_tenant()

    await _insert_message(tenant_id)
    await _insert_message(tenant_id)

    await _insert_message(tenant_id, carrier_id="c-1")
    with pytest.raises(IntegrityError):
        await _insert_message(tenant_id, carrier_id="c-1")

    await _insert_message(tenant_id, carrier="other", carrier_id="c-1")


async def test_invalid_encoding_is_rejected() -> None:
    tenant_id = await _insert_tenant()

    with pytest.raises(IntegrityError):
        await _insert_message(tenant_id, encoding="SMS7")


async def test_invalid_status_is_rejected() -> None:
    tenant_id = await _insert_tenant()

    with pytest.raises(IntegrityError):
        await _insert_message(tenant_id, status="RECEIVED")


async def test_price_arithmetic_is_enforced() -> None:
    tenant_id = await _insert_tenant()

    with pytest.raises(IntegrityError):
        await _insert_message(tenant_id, segments=2, total_price_cents=5)


async def test_invalid_ledger_kind_is_rejected() -> None:
    tenant_id = await _insert_tenant()
    message_id = await _insert_message(tenant_id)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "BONUS", -3, 997)


async def test_ledger_sign_must_match_kind() -> None:
    tenant_id = await _insert_tenant()
    message_id = await _insert_message(tenant_id)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "CHARGE", 3, 1003)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "REFUND", -3, 997)


async def test_charge_requires_a_message_and_credit_forbids_one() -> None:
    tenant_id = await _insert_tenant()
    message_id = await _insert_message(tenant_id)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, None, "CHARGE", -3, 997)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_id, message_id, "CREDIT", 3, 1003)

    await _insert_ledger(tenant_id, None, "CREDIT", 50, 1050)


async def test_cross_tenant_ledger_is_rejected() -> None:
    tenant_a = await _insert_tenant("Tenant A")
    tenant_b = await _insert_tenant("Tenant B")
    message_a = await _insert_message(tenant_a)

    with pytest.raises(IntegrityError):
        await _insert_ledger(tenant_b, message_a, "CHARGE", -3, 997)


async def test_message_requires_an_existing_tenant() -> None:
    with pytest.raises(IntegrityError):
        await _insert_message(uuid.uuid4())


async def test_required_indexes_exist() -> None:
    expected = {
        "idx_tenant_id_created_at",
        "idx_status_available_at",
        "idx_carrier_id",
        "idx_tenant_id_status",
        "idx_message_id_id",
        "idx_ledger_entries_tenant_id_created_at",
        "idx_carrier_carrier_id",
    }

    async with get_engine().connect() as connection:
        names = set(
            (
                await connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                )
            ).scalars()
        )
        definition = (
            await connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'idx_carrier_carrier_id'"
                )
            )
        ).scalar_one()

    assert expected <= names
    assert "UNIQUE INDEX" in definition
    assert "carrier_id IS NOT NULL" in definition


async def test_constraint_names_are_stable() -> None:
    expected = {
        "ck_tenants_opening_balance_non_negative",
        "ck_tenants_balance_non_negative",
        "ck_api_keys_key_hash_sha256_hex",
        "ck_prices_prefix_e164",
        "ck_messages_to_number_e164",
        "ck_messages_sender_id_alphanumeric",
        "ck_messages_text_not_empty",
        "ck_messages_encoding_valid",
        "ck_messages_total_price_arithmetic",
        "ck_messages_status_valid",
        "ck_message_events_status_valid",
        "ck_ledger_entries_kind_valid",
        "ck_ledger_entries_kind_amount_sign",
        "ck_ledger_entries_kind_message_presence",
        "fk_ledger_entries_message_id_tenant_id_messages",
    }

    async with get_engine().connect() as connection:
        names = set(
            (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE connamespace = 'public'::regnamespace"
                    )
                )
            ).scalars()
        )

    assert expected <= names
