"""add auditable inventory documents

Revision ID: c7d8e9f0a1b2
Revises: a5c0722bfbbc, b4d3c2e1f0a9
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = ("a5c0722bfbbc", "b4d3c2e1f0a9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=50), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("surplus_account_id", sa.Integer()),
        sa.Column("shortage_account_id", sa.Integer()),
        sa.Column("journal_id", sa.Integer()),
        sa.Column("reversal_journal_id", sa.Integer()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by", sa.Integer()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancellation_reason", sa.Text()),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["surplus_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["shortage_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"]),
        sa.ForeignKeyConstraint(["reversal_journal_id"], ["journals.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_index("ix_inventory_documents_branch_date", "inventory_documents", ["branch_id", "date"])
    op.create_index("ix_inventory_documents_warehouse_id", "inventory_documents", ["warehouse_id"])
    op.create_index("ix_inventory_documents_type_status", "inventory_documents", ["type", "status"])

    op.create_table(
        "inventory_document_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("system_qty", sa.Float(), nullable=False),
        sa.Column("physical_qty", sa.Float()),
        sa.Column("qty_delta", sa.Float(), nullable=False),
        sa.Column("unit_cost", sa.Float(), nullable=False),
        sa.Column("total_cost", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.ForeignKeyConstraint(["document_id"], ["inventory_documents.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "item_id", name="uq_inventory_document_item"),
    )
    op.create_index("ix_inventory_document_lines_item_id", "inventory_document_lines", ["item_id"])

    op.create_table(
        "inventory_document_batch_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer()),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("unit_cost", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["line_id"], ["inventory_document_lines.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["stock_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_doc_batch_line_id", "inventory_document_batch_allocations", ["line_id"])
    op.create_index("ix_inventory_doc_batch_batch_id", "inventory_document_batch_allocations", ["batch_id"])

    with op.batch_alter_table("stock_batches") as batch_op:
        batch_op.add_column(sa.Column("source_inventory_line_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_stock_batches_source_inventory_line",
            "inventory_document_lines",
            ["source_inventory_line_id"],
            ["id"],
        )
        batch_op.create_index("ix_stock_batches_source_inventory_line_id", ["source_inventory_line_id"])


def downgrade() -> None:
    with op.batch_alter_table("stock_batches") as batch_op:
        batch_op.drop_index("ix_stock_batches_source_inventory_line_id")
        batch_op.drop_constraint("fk_stock_batches_source_inventory_line", type_="foreignkey")
        batch_op.drop_column("source_inventory_line_id")
    op.drop_table("inventory_document_batch_allocations")
    op.drop_table("inventory_document_lines")
    op.drop_table("inventory_documents")
