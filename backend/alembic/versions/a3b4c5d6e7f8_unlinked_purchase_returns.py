"""Allow audited supplier returns without a purchase invoice.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""
from alembic import op
import sqlalchemy as sa


revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_returns") as batch:
        batch.alter_column("purchase_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("supplier_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("return_mode", sa.String(20), nullable=False, server_default="invoice"))
        batch.add_column(sa.Column("evidence_reference", sa.String(255), nullable=True))
        batch.create_foreign_key("fk_purchase_returns_supplier", "suppliers", ["supplier_id"], ["id"])
        batch.create_index("ix_purchase_returns_supplier_id", ["supplier_id"])
    op.execute(
        "UPDATE purchase_returns SET supplier_id = "
        "(SELECT supplier_id FROM purchases WHERE purchases.id = purchase_returns.purchase_id) "
        "WHERE supplier_id IS NULL"
    )


def downgrade():
    with op.batch_alter_table("purchase_returns") as batch:
        batch.drop_index("ix_purchase_returns_supplier_id")
        batch.drop_constraint("fk_purchase_returns_supplier", type_="foreignkey")
        batch.drop_column("evidence_reference")
        batch.drop_column("return_mode")
        batch.drop_column("supplier_id")
        batch.alter_column("purchase_id", existing_type=sa.Integer(), nullable=False)
