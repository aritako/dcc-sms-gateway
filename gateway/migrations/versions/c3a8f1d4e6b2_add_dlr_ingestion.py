"""add DLR ingestion

Revision ID: c3a8f1d4e6b2
Revises: a56d3be64546
Create Date: 2026-11-21 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3a8f1d4e6b2"
down_revision: Union[str, Sequence[str], None] = "a56d3be64546"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column(
    "messages",
    sa.Column("final_at", sa.DateTime(timezone=True), nullable=True),
  )
  op.add_column(
    "message_events",
    sa.Column(
      "source",
      sa.String(length=16),
      server_default=sa.text("'gateway'"),
      nullable=False,
    ),
  )
  op.add_column(
    "message_events",
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
  )
  op.add_column(
    "message_events",
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
  )

  op.create_table(
    "dlr_inbox",
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column(
      "carrier",
      sa.String(length=64),
      server_default=sa.text("'default'"),
      nullable=False,
    ),
    sa.Column("carrier_id", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
      "received_at",
      sa.DateTime(timezone=True),
      server_default=sa.text("now()"),
      nullable=False,
    ),
    sa.Column(
      "state",
      sa.String(length=16),
      server_default=sa.text("'PENDING'"),
      nullable=False,
    ),
    sa.Column("rejection_reason", sa.Text(), nullable=True),
    sa.CheckConstraint(
      "status IN ('DELIVERED', 'UNDELIVERED', 'EXPIRED')",
      name="ck_dlr_inbox_status_terminal",
    ),
    sa.CheckConstraint(
      "state IN ('PENDING', 'PROCESSED', 'RECONCILED', 'REJECTED')",
      name="ck_dlr_inbox_state_valid",
    ),
    sa.CheckConstraint("char_length(carrier) > 0", name="ck_dlr_inbox_carrier_not_empty"),
    sa.CheckConstraint(
      "char_length(carrier_id) > 0", name="ck_dlr_inbox_carrier_id_not_empty"
    ),
    sa.PrimaryKeyConstraint("id", name="pk_dlr_inbox"),
    sa.UniqueConstraint(
      "carrier",
      "carrier_id",
      "status",
      "occurred_at",
      name="uq_dlr_inbox_carrier_carrier_id_status_occurred_at",
    ),
  )
  op.create_index(
    "idx_dlr_inbox_state_carrier_carrier_id",
    "dlr_inbox",
    ["state", "carrier", "carrier_id"],
  )


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_index("idx_dlr_inbox_state_carrier_carrier_id", table_name="dlr_inbox")
  op.drop_table("dlr_inbox")
  op.drop_column("message_events", "received_at")
  op.drop_column("message_events", "occurred_at")
  op.drop_column("message_events", "source")
  op.drop_column("messages", "final_at")
