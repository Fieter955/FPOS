"""add customer deposit movement ledger

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    trade_columns = {column["name"] for column in inspector.get_columns("trade_ins")}
    if "status" not in trade_columns:
        op.add_column("trade_ins", sa.Column("status", sa.String(20), nullable=False, server_default="completed"))
    if "cancelled_at" not in trade_columns:
        op.add_column("trade_ins", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    if inspector.has_table("customer_deposit_movements"):
        return
    op.create_table(
        "customer_deposit_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("balance_after", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_customer_deposit_movements_customer_time", "customer_deposit_movements", ["customer_id", "occurred_at"])
    op.create_index("ix_customer_deposit_movements_branch_id", "customer_deposit_movements", ["branch_id"])
    op.create_index("ix_customer_deposit_movements_source", "customer_deposit_movements", ["source_type", "source_id"])


def downgrade():
    op.drop_table("customer_deposit_movements")
    op.drop_column("trade_ins", "cancelled_at")
    op.drop_column("trade_ins", "status")
