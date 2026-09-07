"""add trade-in customer deposit tender

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("trade_ins")}
    if "deposit_amount" not in columns:
        op.add_column(
            "trade_ins",
            sa.Column("deposit_amount", sa.Float(), nullable=True, server_default="0"),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("trade_ins")}
    if "deposit_amount" in columns:
        op.drop_column("trade_ins", "deposit_amount")
