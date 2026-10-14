"""Record the unit entered on purchase lines.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""
from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("purchase_items")}
    additions = []
    if "entered_item_id" not in columns:
        additions.append(sa.Column("entered_item_id", sa.Integer(), nullable=True))
    if "entered_qty" not in columns:
        additions.append(sa.Column("entered_qty", sa.Float(), nullable=True))
    if "conversion_factor" not in columns:
        additions.append(sa.Column("conversion_factor", sa.Float(), nullable=False, server_default="1"))
    if additions:
        with op.batch_alter_table("purchase_items") as batch:
            for column in additions:
                batch.add_column(column)

    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("purchase_items")}
    if "ix_purchase_items_entered_item_id" not in indexes:
        op.create_index("ix_purchase_items_entered_item_id", "purchase_items", ["entered_item_id"])


def downgrade():
    op.drop_index("ix_purchase_items_entered_item_id", table_name="purchase_items")
    with op.batch_alter_table("purchase_items") as batch:
        batch.drop_constraint("fk_purchase_items_entered_item", type_="foreignkey")
        batch.drop_column("conversion_factor")
        batch.drop_column("entered_qty")
        batch.drop_column("entered_item_id")
