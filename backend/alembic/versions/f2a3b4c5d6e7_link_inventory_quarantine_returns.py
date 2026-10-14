"""Link inventory quarantine to purchase return lines.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("purchase_return_items")}
    if "inventory_quarantine_id" not in columns:
        with op.batch_alter_table("purchase_return_items") as batch:
            batch.add_column(sa.Column("inventory_quarantine_id", sa.Integer(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("purchase_return_items")}
    if "ix_purchase_return_items_quarantine" not in indexes:
        op.create_index("ix_purchase_return_items_quarantine", "purchase_return_items", ["inventory_quarantine_id"])


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("purchase_return_items")}
    if "ix_purchase_return_items_quarantine" in indexes:
        op.drop_index("ix_purchase_return_items_quarantine", table_name="purchase_return_items")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("purchase_return_items")}
    if "inventory_quarantine_id" in columns:
        with op.batch_alter_table("purchase_return_items") as batch:
            batch.drop_column("inventory_quarantine_id")
