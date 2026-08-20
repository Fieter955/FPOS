"""harden receipt printing and persist payment snapshots

Revision ID: e8f9a0b1c2d3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table, column):
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    for column in (
        sa.Column("receipt_name", sa.String(length=150), nullable=True),
        sa.Column("receipt_footer", sa.String(length=500), nullable=True, server_default="Terima kasih telah berbelanja!"),
        sa.Column("receipt_paper_width_mm", sa.Integer(), nullable=True, server_default="80"),
        sa.Column("receipt_auto_print", sa.Boolean(), nullable=True, server_default=sa.false()),
    ):
        _add("branches", column)

    _add("sales", sa.Column("cash_received", sa.Float(), nullable=True))
    _add("sales", sa.Column("invoice_discount_gross", sa.Float(), nullable=True))
    _add("trade_ins", sa.Column("cash_amount", sa.Float(), nullable=True, server_default="0"))
    _add("trade_ins", sa.Column("bank_amount", sa.Float(), nullable=True, server_default="0"))

    for table in ("sale_returns", "purchase_returns"):
        _add(table, sa.Column("branch_id", sa.Integer(), nullable=True))
        _add(table, sa.Column("created_by", sa.Integer(), nullable=True))

    for column in (
        sa.Column("document_type", sa.String(length=40), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    ):
        _add("print_jobs", column)

    if "sale_payments" not in tables:
        op.create_table(
            "sale_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False),
            sa.Column("method", sa.String(length=30), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_sale_payments_sale_id", "sale_payments", ["sale_id"])

    if "printer_agents" not in tables:
        op.create_table(
            "printer_agents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False, server_default="Printer Utama"),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("token_last4", sa.String(length=4), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("branch_id", name="uq_printer_agents_branch_id"),
        )
        op.create_index("ix_printer_agents_branch_id", "printer_agents", ["branch_id"])

    existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("print_jobs")}
    if "ix_print_jobs_branch_id" not in existing_indexes:
        op.create_index("ix_print_jobs_branch_id", "print_jobs", ["branch_id"])
    if "ix_print_jobs_branch_status" not in existing_indexes:
        op.create_index("ix_print_jobs_branch_status", "print_jobs", ["branch_id", "status"])

    op.execute(
        "UPDATE sale_returns SET branch_id = "
        "(SELECT branch_id FROM sales WHERE sales.id = sale_returns.sale_id) "
        "WHERE branch_id IS NULL"
    )
    op.execute(
        "UPDATE purchase_returns SET branch_id = "
        "(SELECT branch_id FROM purchases WHERE purchases.id = purchase_returns.purchase_id) "
        "WHERE branch_id IS NULL"
    )
    op.execute(
        "UPDATE print_jobs SET branch_id = (SELECT MIN(id) FROM branches) "
        "WHERE branch_id IS NULL"
    )
    op.execute("UPDATE print_jobs SET attempt_count = 0 WHERE attempt_count IS NULL")
    op.execute(
        "UPDATE print_jobs SET status = 'pending' "
        "WHERE status = 'processing' AND lease_until IS NULL"
    )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "printer_agents" in tables:
        op.drop_table("printer_agents")
    if "sale_payments" in tables:
        op.drop_table("sale_payments")

    for name in (
        "last_error", "completed_at", "lease_until", "claimed_at", "attempt_count",
        "created_by", "document_id", "document_type",
    ):
        if name in _columns("print_jobs"):
            op.drop_column("print_jobs", name)
    for table in ("sale_returns", "purchase_returns"):
        for name in ("created_by", "branch_id"):
            if name in _columns(table):
                op.drop_column(table, name)
    for name in ("bank_amount", "cash_amount"):
        if name in _columns("trade_ins"):
            op.drop_column("trade_ins", name)
    for name in ("invoice_discount_gross", "cash_received"):
        if name in _columns("sales"):
            op.drop_column("sales", name)
    for name in ("receipt_auto_print", "receipt_paper_width_mm", "receipt_footer", "receipt_name"):
        if name in _columns("branches"):
            op.drop_column("branches", name)
