from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    DELIVERED = "DELIVERED"
    UNDELIVERED = "UNDELIVERED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class Encoding(StrEnum):
    GSM7 = "GSM7"
    UCS2 = "UCS2"


class LedgerKind(StrEnum):
    CHARGE = "CHARGE"
    REFUND = "REFUND"
    CREDIT = "CREDIT"


def _allowed_values(column: str, values: type[StrEnum]) -> str:
    """Render a CHECK-friendly IN list from an enum, e.g. status IN ('ACCEPTED', ...)."""
    rendered = ", ".join(f"'{value.value}'" for value in values)
    return f"{column} IN ({rendered})"


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "opening_balance_cents >= 0",
            name="ck_tenants_opening_balance_non_negative",
        ),
        CheckConstraint("balance_cents >= 0", name="ck_tenants_balance_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_tenants_currency_iso_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(Text, unique=True)
    opening_balance_cents: Mapped[int] = mapped_column(BigInteger)
    balance_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), server_default=sa_text("'USD'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_api_keys_key_hash_sha256_hex",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint("prefix ~ '^\\+[1-9][0-9]{0,14}$'", name="ck_prices_prefix_e164"),
        CheckConstraint(
            "price_per_segment_cents > 0",
            name="ck_prices_price_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    price_per_segment_cents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "to_number ~ '^\\+[1-9][0-9]{1,14}$'",
            name="ck_messages_to_number_e164",
        ),
        CheckConstraint(
            "sender_id ~ '^[A-Za-z0-9]{1,11}$'",
            name="ck_messages_sender_id_alphanumeric",
        ),
        CheckConstraint("char_length(text) > 0", name="ck_messages_text_not_empty"),
        CheckConstraint(
            _allowed_values("encoding", Encoding),
            name="ck_messages_encoding_valid",
        ),
        CheckConstraint("segments >= 1", name="ck_messages_segments_positive"),
        CheckConstraint(
            "price_per_segment_cents >= 0",
            name="ck_messages_price_per_segment_non_negative",
        ),
        CheckConstraint(
            "total_price_cents = segments * price_per_segment_cents",
            name="ck_messages_total_price_arithmetic",
        ),
        CheckConstraint(_allowed_values("status", MessageStatus), name="ck_messages_status_valid"),
        CheckConstraint("char_length(carrier) > 0", name="ck_messages_carrier_not_empty"),
        CheckConstraint(
            "carrier_id IS NULL OR char_length(carrier_id) > 0",
            name="ck_messages_carrier_id_not_empty",
        ),
        UniqueConstraint("id", "tenant_id"),
        Index(
            "idx_carrier_carrier_id",
            "carrier",
            "carrier_id",
            unique=True,
            postgresql_where=sa_text("carrier_id IS NOT NULL"),
        ),
        Index("idx_tenant_id_created_at", "tenant_id", sa_text("created_at DESC")),
        Index("idx_status_available_at", "status", "available_at"),
        Index("idx_tenant_id_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    to_number: Mapped[str] = mapped_column(String(16))
    sender_id: Mapped[str] = mapped_column(String(11))
    text: Mapped[str] = mapped_column(Text)
    encoding: Mapped[str] = mapped_column(String(4))
    segments: Mapped[int] = mapped_column(Integer)
    price_per_segment_cents: Mapped[int] = mapped_column(BigInteger)
    total_price_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(16), default=MessageStatus.ACCEPTED.value, server_default=sa_text("'ACCEPTED'")
    )
    carrier: Mapped[str] = mapped_column(String(64), server_default=sa_text("'default'"))
    carrier_id: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageEvent(Base):
    __tablename__ = "message_events"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("status", MessageStatus),
            name="ck_message_events_status_valid",
        ),
        UniqueConstraint("message_id", "status"),
        Index("idx_message_id_id", "message_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(_allowed_values("kind", LedgerKind), name="ck_ledger_entries_kind_valid"),
        CheckConstraint("amount_cents <> 0", name="ck_ledger_entries_amount_non_zero"),
        CheckConstraint(
            "balance_after_cents >= 0",
            name="ck_ledger_entries_balance_after_non_negative",
        ),
        CheckConstraint(
            "(kind = 'CHARGE' AND amount_cents < 0) OR "
            "(kind IN ('REFUND', 'CREDIT') AND amount_cents > 0)",
            name="ck_ledger_entries_kind_amount_sign",
        ),
        CheckConstraint(
            "(kind IN ('CHARGE', 'REFUND') AND message_id IS NOT NULL) OR "
            "(kind = 'CREDIT' AND message_id IS NULL)",
            name="ck_ledger_entries_kind_message_presence",
        ),
        UniqueConstraint("message_id", "kind"),
        ForeignKeyConstraint(
            ["message_id", "tenant_id"],
            ["messages.id", "messages.tenant_id"],
            name="fk_ledger_entries_message_id_tenant_id_messages",
        ),
        Index("idx_ledger_entries_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(8))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_after_cents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
