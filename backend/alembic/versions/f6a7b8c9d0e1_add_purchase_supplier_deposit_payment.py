"""add supplier deposit portion to purchase payments

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("purchases")}
    if "deposit_paid" not in columns:
        with op.batch_alter_table("purchases") as batch:
            batch.add_column(
                sa.Column("deposit_paid", sa.Float(), nullable=False, server_default="0")
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("purchases")}
    if "deposit_paid" in columns:
        with op.batch_alter_table("purchases") as batch:
            batch.drop_column("deposit_paid")
