"""add canonical purchase tax type

Revision ID: b4d3c2e1f0a9
Revises: 9419282e6bd5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4d3c2e1f0a9"
down_revision: Union[str, None] = "9419282e6bd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tax_type", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.drop_column("tax_type")
