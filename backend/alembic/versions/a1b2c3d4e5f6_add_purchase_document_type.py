"""add document type to purchases

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("document_type", sa.String(length=20), nullable=True)
        )
    op.execute("UPDATE purchases SET document_type = 'branch_request' WHERE is_branch_request = 1")
    op.execute("UPDATE purchases SET document_type = 'purchase' WHERE document_type IS NULL")
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.alter_column("document_type", existing_type=sa.String(length=20), nullable=False, server_default="purchase")


def downgrade():
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.drop_column("document_type")
