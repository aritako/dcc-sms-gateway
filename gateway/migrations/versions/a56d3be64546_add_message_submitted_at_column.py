"""add message submitted_at column

Revision ID: a56d3be64546
Revises: 3bb3f16f92dd
Create Date: 2026-11-20 16:01:22.484823

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a56d3be64546'
down_revision: Union[str, Sequence[str], None] = '3bb3f16f92dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column("messages", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_column("messages", "submitted_at")
