"""stabilize branch PO fulfillment and transfer provenance

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchases") as batch:
        batch.add_column(sa.Column("fulfillment_status", sa.String(20), nullable=False, server_default="none"))
    with op.batch_alter_table("purchase_items") as batch:
        batch.add_column(sa.Column("source_request_item_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_purchase_item_source_request", "purchase_items", ["source_request_item_id"], ["id"])
        batch.create_index("ix_purchase_items_source_request", ["source_request_item_id"])
    with op.batch_alter_table("warehouse_transfers") as batch:
        batch.add_column(sa.Column("transfer_kind", sa.String(30), nullable=False, server_default="standard"))
        batch.add_column(sa.Column("source_purchase_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("received_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_transfer_source_purchase", "purchases", ["source_purchase_id"], ["id"])
        batch.create_index("ix_transfer_source_purchase", ["source_purchase_id"])
    with op.batch_alter_table("warehouse_transfer_items") as batch:
        batch.add_column(sa.Column("qty_received", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("purchase_item_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_request_item_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("unit_cost", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("total_cost", sa.Float(), nullable=False, server_default="0"))
        batch.create_foreign_key("fk_transfer_item_purchase_item", "purchase_items", ["purchase_item_id"], ["id"])
        batch.create_foreign_key("fk_transfer_item_request_item", "purchase_items", ["source_request_item_id"], ["id"])
    op.execute(sa.text("""
        UPDATE purchase_items AS child
        SET source_request_item_id = (
            SELECT src.id FROM purchases AS p
            JOIN purchase_items AS src ON src.purchase_id = p.from_po_id
            WHERE p.id = child.purchase_id AND src.item_id = child.item_id
            LIMIT 1
        )
        WHERE child.purchase_id IN (SELECT id FROM purchases WHERE from_po_id IS NOT NULL)
          AND 1 = (
            SELECT COUNT(*) FROM purchases AS p2
            JOIN purchase_items AS src2 ON src2.purchase_id = p2.from_po_id
            WHERE p2.id = child.purchase_id AND src2.item_id = child.item_id
          )
    """))


def downgrade():
    with op.batch_alter_table("warehouse_transfer_items") as batch:
        batch.drop_constraint("fk_transfer_item_request_item", type_="foreignkey")
        batch.drop_constraint("fk_transfer_item_purchase_item", type_="foreignkey")
        batch.drop_column("total_cost")
        batch.drop_column("unit_cost")
        batch.drop_column("source_request_item_id")
        batch.drop_column("purchase_item_id")
        batch.drop_column("qty_received")
    with op.batch_alter_table("warehouse_transfers") as batch:
        batch.drop_index("ix_transfer_source_purchase")
        batch.drop_constraint("fk_transfer_source_purchase", type_="foreignkey")
        batch.drop_column("received_at")
        batch.drop_column("source_purchase_id")
        batch.drop_column("transfer_kind")
    with op.batch_alter_table("purchase_items") as batch:
        batch.drop_index("ix_purchase_items_source_request")
        batch.drop_constraint("fk_purchase_item_source_request", type_="foreignkey")
        batch.drop_column("source_request_item_id")
    with op.batch_alter_table("purchases") as batch:
        batch.drop_column("fulfillment_status")
