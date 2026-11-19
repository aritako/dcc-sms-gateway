"""idempotency keys

Revision ID: e40134aacff4
Revises: 0d3c682e1245
Create Date: 2026-11-19 23:28:30.726320

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e40134aacff4"
down_revision: Union[str, Sequence[str], None] = "0d3c682e1245"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.create_table(
    "idempotency_keys",
    sa.Column("tenant_id", sa.Uuid(), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("request_hash", sa.String(length=64), nullable=False),
    sa.Column("response_status", sa.Integer(), nullable=True),
    sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column(
      "created_at",
      sa.DateTime(timezone=True),
      server_default=sa.text("now()"),
      nullable=False,
    ),
    sa.CheckConstraint(
      "(response_status IS NULL) = (response_body IS NULL)",
      name="ck_idempotency_keys_response_complete",
    ),
    sa.ForeignKeyConstraint(
      ["tenant_id"],
      ["tenants.id"],
      name="fk_idempotency_keys_tenant_id_tenants",
      ondelete="CASCADE",
    ),
    sa.PrimaryKeyConstraint("tenant_id", "key", name="pk_idempotency_keys"),
  )


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_table("idempotency_keys")
