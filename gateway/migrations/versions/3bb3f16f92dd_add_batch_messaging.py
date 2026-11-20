"""add batch messaging

Revision ID: 3bb3f16f92dd
Revises: e40134aacff4
Create Date: 2026-11-20 03:17:32.635193

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3bb3f16f92dd"
down_revision: Union[str, Sequence[str], None] = "e40134aacff4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column("messages", sa.Column("batch_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_column("messages", "batch_id")
