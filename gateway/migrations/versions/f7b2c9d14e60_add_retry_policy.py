"""add retry policy, attempts and billing review state

Revision ID: f7b2c9d14e60
Revises: c3a8f1d4e6b2
Create Date: 2026-11-21 20:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7b2c9d14e60"
down_revision: Union[str, Sequence[str], None] = "c3a8f1d4e6b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column(
    "messages",
    sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
  )
  op.add_column(
    "messages",
    sa.Column(
      "billing_state",
      sa.String(length=16),
      server_default=sa.text("'NORMAL'"),
      nullable=False,
    ),
  )
  op.create_check_constraint(
    "ck_messages_billing_state_valid",
    "messages",
    "billing_state IN ('NORMAL', 'REVIEW')",
  )

  op.create_table(
    "submit_attempts",
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("message_id", sa.Uuid(), nullable=False),
    sa.Column("attempt_number", sa.Integer(), nullable=False),
    sa.Column("outcome", sa.String(length=16), nullable=False),
    sa.Column("http_status", sa.Integer(), nullable=True),
    sa.Column("error", sa.Text(), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
      "attempt_number >= 1",
      name="ck_submit_attempts_number_positive",
    ),
    sa.CheckConstraint(
      "outcome IN ('ACCEPTED', 'PERMANENT', 'TRANSIENT', 'AMBIGUOUS')",
      name="ck_submit_attempts_outcome_valid",
    ),
    sa.CheckConstraint(
      "http_status IS NULL OR http_status BETWEEN 100 AND 599",
      name="ck_submit_attempts_http_status_valid",
    ),
    sa.ForeignKeyConstraint(
      ["message_id"],
      ["messages.id"],
      name="fk_submit_attempts_message_id_messages",
      ondelete="CASCADE",
    ),
    sa.PrimaryKeyConstraint("id", name="pk_submit_attempts"),
    sa.UniqueConstraint(
      "message_id",
      "attempt_number",
      name="uq_submit_attempts_message_id_attempt_number",
    ),
  )


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_table("submit_attempts")
  op.drop_constraint("ck_messages_billing_state_valid", "messages", type_="check")
  op.drop_column("messages", "billing_state")
  op.drop_column("messages", "next_attempt_at")
