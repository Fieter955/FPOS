"""link return rows to their exact invoice rows

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("warehouse_stocks")
    }
    unique_index_names = {
        index["name"] for index in inspector.get_indexes("warehouse_stocks") if index.get("unique")
    }
    if "uq_warehouse_stocks_wh_item" not in unique_names | unique_index_names:
        with op.batch_alter_table("warehouse_stocks") as batch:
            batch.create_unique_constraint(
                "uq_warehouse_stocks_wh_item", ["warehouse_id", "item_id"]
            )
    if "sale_item_id" not in _columns("sale_return_items"):
        with op.batch_alter_table("sale_return_items") as batch:
            batch.add_column(sa.Column("sale_item_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_sale_return_items_sale_item_id", "sale_items", ["sale_item_id"], ["id"]
            )
            batch.create_index("ix_sale_return_items_sale_item_id", ["sale_item_id"])
    if "purchase_item_id" not in _columns("purchase_return_items"):
        with op.batch_alter_table("purchase_return_items") as batch:
            batch.add_column(sa.Column("purchase_item_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_purchase_return_items_purchase_item_id", "purchase_items", ["purchase_item_id"], ["id"]
            )
            batch.create_index("ix_purchase_return_items_purchase_item_id", ["purchase_item_id"])
    if "stock_batch_id" not in _columns("trade_in_return_items"):
        with op.batch_alter_table("trade_in_return_items") as batch:
            batch.add_column(sa.Column("stock_batch_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_trade_in_return_items_stock_batch_id", "stock_batches", ["stock_batch_id"], ["id"]
            )
    if "trade_in_new_item_batches" not in inspector.get_table_names():
        op.create_table(
            "trade_in_new_item_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("trade_in_new_item_id", sa.Integer(), sa.ForeignKey("trade_in_new_items.id"), nullable=False),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("stock_batches.id"), nullable=False),
            sa.Column("qty", sa.Float(), nullable=False),
            sa.Column("unit_cost", sa.Float(), nullable=False, server_default="0"),
        )
        op.create_index("ix_trade_in_new_item_batches_trade_in_new_item_id", "trade_in_new_item_batches", ["trade_in_new_item_id"])
        op.create_index("ix_trade_in_new_item_batches_batch_id", "trade_in_new_item_batches", ["batch_id"])


def downgrade():
    with op.batch_alter_table("warehouse_stocks") as batch:
        batch.drop_constraint("uq_warehouse_stocks_wh_item", type_="unique")
    op.drop_index("ix_trade_in_new_item_batches_batch_id", table_name="trade_in_new_item_batches")
    op.drop_index("ix_trade_in_new_item_batches_trade_in_new_item_id", table_name="trade_in_new_item_batches")
    op.drop_table("trade_in_new_item_batches")
    if "stock_batch_id" in _columns("trade_in_return_items"):
        with op.batch_alter_table("trade_in_return_items") as batch:
            batch.drop_constraint("fk_trade_in_return_items_stock_batch_id", type_="foreignkey")
            batch.drop_column("stock_batch_id")
    if "purchase_item_id" in _columns("purchase_return_items"):
        with op.batch_alter_table("purchase_return_items") as batch:
            batch.drop_index("ix_purchase_return_items_purchase_item_id")
            batch.drop_constraint("fk_purchase_return_items_purchase_item_id", type_="foreignkey")
            batch.drop_column("purchase_item_id")
    if "sale_item_id" in _columns("sale_return_items"):
        with op.batch_alter_table("sale_return_items") as batch:
            batch.drop_index("ix_sale_return_items_sale_item_id")
            batch.drop_constraint("fk_sale_return_items_sale_item_id", type_="foreignkey")
            batch.drop_column("sale_item_id")
