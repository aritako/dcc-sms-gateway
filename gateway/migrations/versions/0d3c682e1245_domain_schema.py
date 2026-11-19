"""domain schema

Revision ID: 0d3c682e1245
Revises: 51282902a726
Create Date: 2026-11-19 01:23:29.901080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d3c682e1245'
down_revision: Union[str, Sequence[str], None] = '51282902a726'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("opening_balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "opening_balance_cents >= 0",
            name="ck_tenants_opening_balance_non_negative",
        ),
        sa.CheckConstraint("balance_cents >= 0", name="ck_tenants_balance_non_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_tenants_currency_iso_format"),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("name", name="uq_tenants_name"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'", name="ck_api_keys_key_hash_sha256_hex"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_api_keys_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )

    op.create_table(
        "prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("price_per_segment_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("prefix ~ '^\\+[1-9][0-9]{0,14}$'", name="ck_prices_prefix_e164"),
        sa.CheckConstraint(
            "price_per_segment_cents > 0", name="ck_prices_price_positive"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prices"),
        sa.UniqueConstraint("prefix", name="uq_prices_prefix"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("to_number", sa.String(length=16), nullable=False),
        sa.Column("sender_id", sa.String(length=11), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("encoding", sa.String(length=4), nullable=False),
        sa.Column("segments", sa.Integer(), nullable=False),
        sa.Column("price_per_segment_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_price_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ACCEPTED'"),
            nullable=False,
        ),
        sa.Column(
            "carrier",
            sa.String(length=64),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column("carrier_id", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "to_number ~ '^\\+[1-9][0-9]{1,14}$'", name="ck_messages_to_number_e164"
        ),
        sa.CheckConstraint(
            "sender_id ~ '^[A-Za-z0-9]{1,11}$'", name="ck_messages_sender_id_alphanumeric"
        ),
        sa.CheckConstraint("char_length(text) > 0", name="ck_messages_text_not_empty"),
        sa.CheckConstraint(
            "encoding IN ('GSM7', 'UCS2')", name="ck_messages_encoding_valid"
        ),
        sa.CheckConstraint("segments >= 1", name="ck_messages_segments_positive"),
        sa.CheckConstraint(
            "price_per_segment_cents >= 0",
            name="ck_messages_price_per_segment_non_negative",
        ),
        sa.CheckConstraint(
            "total_price_cents = segments * price_per_segment_cents",
            name="ck_messages_total_price_arithmetic",
        ),
        sa.CheckConstraint(
            "status IN ('ACCEPTED', 'QUEUED', 'SUBMITTED', 'DELIVERED', "
            "'UNDELIVERED', 'EXPIRED', 'FAILED')",
            name="ck_messages_status_valid",
        ),
        sa.CheckConstraint("char_length(carrier) > 0", name="ck_messages_carrier_not_empty"),
        sa.CheckConstraint(
            "carrier_id IS NULL OR char_length(carrier_id) > 0",
            name="ck_messages_carrier_id_not_empty",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_messages_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_messages_id_tenant_id"),
    )
    op.create_index(
        "idx_tenant_id_created_at",
        "messages",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_status_available_at", "messages", ["status", "available_at"]
    )
    op.create_index("idx_carrier_id", "messages", ["carrier_id"])
    op.create_index("idx_tenant_id_status", "messages", ["tenant_id", "status"])
    op.create_index(
        "idx_carrier_carrier_id",
        "messages",
        ["carrier", "carrier_id"],
        unique=True,
        postgresql_where=sa.text("carrier_id IS NOT NULL"),
    )

    op.create_table(
        "message_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACCEPTED', 'QUEUED', 'SUBMITTED', 'DELIVERED', "
            "'UNDELIVERED', 'EXPIRED', 'FAILED')",
            name="ck_message_events_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_message_events_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_message_events"),
        sa.UniqueConstraint(
            "message_id", "status", name="uq_message_events_message_id_status"
        ),
    )
    op.create_index(
        "idx_message_id_id", "message_events", ["message_id", "id"]
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('CHARGE', 'REFUND', 'CREDIT')", name="ck_ledger_entries_kind_valid"
        ),
        sa.CheckConstraint(
            "amount_cents <> 0", name="ck_ledger_entries_amount_non_zero"
        ),
        sa.CheckConstraint(
            "balance_after_cents >= 0",
            name="ck_ledger_entries_balance_after_non_negative",
        ),
        sa.CheckConstraint(
            "(kind = 'CHARGE' AND amount_cents < 0) OR "
            "(kind IN ('REFUND', 'CREDIT') AND amount_cents > 0)",
            name="ck_ledger_entries_kind_amount_sign",
        ),
        sa.CheckConstraint(
            "(kind IN ('CHARGE', 'REFUND') AND message_id IS NOT NULL) OR "
            "(kind = 'CREDIT' AND message_id IS NULL)",
            name="ck_ledger_entries_kind_message_presence",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "tenant_id"],
            ["messages.id", "messages.tenant_id"],
            name="fk_ledger_entries_message_id_tenant_id_messages",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_ledger_entries_tenant_id_tenants"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_entries"),
        sa.UniqueConstraint(
            "message_id", "kind", name="uq_ledger_entries_message_id_kind"
        ),
    )
    op.create_index(
        "idx_ledger_entries_tenant_id_created_at",
        "ledger_entries",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ledger_entries")
    op.drop_table("message_events")
    op.drop_table("messages")
    op.drop_table("prices")
    op.drop_table("api_keys")
    op.drop_table("tenants")
