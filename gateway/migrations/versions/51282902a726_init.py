"""init

Revision ID: 51282902a726
Revises: 
Create Date: 2026-11-18 13:55:10.589986

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51282902a726'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  pass


def downgrade() -> None:
  """Downgrade schema."""
  pass
