"""add auditable inventory reconciliation

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "inventory_reconciliations" not in existing:
        op.create_table(
            "inventory_reconciliations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("number", sa.String(length=60), nullable=False, unique=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=False),
            sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("gl_before", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_value_before", sa.Float(), nullable=False, server_default="0"),
            sa.Column("gl_after", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_value_after", sa.Float(), nullable=False, server_default="0"),
            sa.Column("journal_id", sa.Integer(), sa.ForeignKey("journals.id"), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_inventory_reconciliations_branch_created", "inventory_reconciliations", ["branch_id", "created_at"])
        op.create_index("ix_inventory_reconciliations_warehouse_id", "inventory_reconciliations", ["warehouse_id"])
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "inventory_reconciliation_lines" not in existing:
        op.create_table(
            "inventory_reconciliation_lines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reconciliation_id", sa.Integer(), sa.ForeignKey("inventory_reconciliations.id"), nullable=False),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
            sa.Column("action", sa.String(length=50), nullable=False),
            sa.Column("warehouse_qty", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_qty_before", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_qty_after", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_value_before", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fifo_value_after", sa.Float(), nullable=False, server_default="0"),
            sa.Column("unit_cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cost_source", sa.String(length=50), nullable=False, server_default="not_applicable"),
            sa.Column("item_stock_before", sa.Float(), nullable=True),
            sa.Column("item_stock_after", sa.Float(), nullable=True),
            sa.UniqueConstraint("reconciliation_id", "item_id", name="uq_inventory_reconciliation_item"),
        )
        op.create_index("ix_inventory_reconciliation_lines_item_id", "inventory_reconciliation_lines", ["item_id"])
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "inventory_reconciliation_batch_actions" not in existing:
        op.create_table(
            "inventory_reconciliation_batch_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("line_id", sa.Integer(), sa.ForeignKey("inventory_reconciliation_lines.id"), nullable=False),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("stock_batches.id"), nullable=False),
            sa.Column("qty_delta", sa.Float(), nullable=False),
            sa.Column("unit_cost", sa.Float(), nullable=False, server_default="0"),
        )
        op.create_index("ix_inventory_reconciliation_batch_line", "inventory_reconciliation_batch_actions", ["line_id"])
        op.create_index("ix_inventory_reconciliation_batch_batch", "inventory_reconciliation_batch_actions", ["batch_id"])


def downgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "inventory_reconciliation_batch_actions" in existing:
        op.drop_table("inventory_reconciliation_batch_actions")
    if "inventory_reconciliation_lines" in existing:
        op.drop_table("inventory_reconciliation_lines")
    if "inventory_reconciliations" in existing:
        op.drop_table("inventory_reconciliations")
