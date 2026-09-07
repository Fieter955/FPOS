"""add damaged-return quarantine tracking

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    sale_columns = _columns("sale_return_items")
    if "condition" not in sale_columns:
        op.add_column("sale_return_items", sa.Column("condition", sa.String(20), nullable=False, server_default="good"))
    if "returned_qty" not in sale_columns:
        op.add_column("sale_return_items", sa.Column("returned_qty", sa.Float(), nullable=False, server_default="0"))
    if "quarantine_unit_cost" not in sale_columns:
        op.add_column("sale_return_items", sa.Column("quarantine_unit_cost", sa.Float(), nullable=False, server_default="0"))

    purchase_columns = _columns("purchase_return_items")
    if "trade_in_return_item_id" not in purchase_columns:
        op.add_column("purchase_return_items", sa.Column("trade_in_return_item_id", sa.Integer(), nullable=True))
    if "sale_return_item_id" not in purchase_columns:
        op.add_column("purchase_return_items", sa.Column("sale_return_item_id", sa.Integer(), nullable=True))
    if "quarantine_cost" not in purchase_columns:
        op.add_column("purchase_return_items", sa.Column("quarantine_cost", sa.Float(), nullable=False, server_default="0"))

    op.execute("UPDATE trade_in_return_items SET condition = 'damaged' WHERE condition = 'partial'")


def downgrade():
    purchase_columns = _columns("purchase_return_items")
    if "quarantine_cost" in purchase_columns:
        op.drop_column("purchase_return_items", "quarantine_cost")
    if "sale_return_item_id" in purchase_columns:
        op.drop_column("purchase_return_items", "sale_return_item_id")
    if "trade_in_return_item_id" in purchase_columns:
        op.drop_column("purchase_return_items", "trade_in_return_item_id")

    sale_columns = _columns("sale_return_items")
    if "quarantine_unit_cost" in sale_columns:
        op.drop_column("sale_return_items", "quarantine_unit_cost")
    if "returned_qty" in sale_columns:
        op.drop_column("sale_return_items", "returned_qty")
    if "condition" in sale_columns:
        op.drop_column("sale_return_items", "condition")
