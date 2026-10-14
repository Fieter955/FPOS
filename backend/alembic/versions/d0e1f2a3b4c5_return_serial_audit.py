"""Add serial provenance and return reversal metadata.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def _add_return_metadata(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("status", sa.String(length=20), nullable=False, server_default="posted"))
        batch.add_column(sa.Column("cancelled_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancellation_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reversal_journal_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(f"fk_{table_name}_cancelled_by", "users", ["cancelled_by"], ["id"])
        batch.create_foreign_key(f"fk_{table_name}_reversal_journal", "journals", ["reversal_journal_id"], ["id"])


def _drop_return_metadata(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(f"fk_{table_name}_reversal_journal", type_="foreignkey")
        batch.drop_constraint(f"fk_{table_name}_cancelled_by", type_="foreignkey")
        batch.drop_column("reversal_journal_id")
        batch.drop_column("cancellation_reason")
        batch.drop_column("cancelled_at")
        batch.drop_column("cancelled_by")
        batch.drop_column("status")


def upgrade() -> None:
    with op.batch_alter_table("sale_items") as batch:
        batch.add_column(sa.Column("serials_json", sa.Text(), nullable=True))
    with op.batch_alter_table("sale_return_items") as batch:
        batch.add_column(sa.Column("serials_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("carrying_cost", sa.Float(), nullable=False, server_default="0"))
    with op.batch_alter_table("purchase_return_items") as batch:
        batch.add_column(sa.Column("serials_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("carrying_cost", sa.Float(), nullable=False, server_default="0"))
    _add_return_metadata("sale_returns")
    _add_return_metadata("purchase_returns")


def downgrade() -> None:
    _drop_return_metadata("purchase_returns")
    _drop_return_metadata("sale_returns")
    with op.batch_alter_table("purchase_return_items") as batch:
        batch.drop_column("carrying_cost")
        batch.drop_column("serials_json")
    with op.batch_alter_table("sale_return_items") as batch:
        batch.drop_column("carrying_cost")
        batch.drop_column("serials_json")
    with op.batch_alter_table("sale_items") as batch:
        batch.drop_column("serials_json")
