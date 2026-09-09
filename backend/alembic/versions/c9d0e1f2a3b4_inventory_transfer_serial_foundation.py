"""inventory transfer and serial foundation

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    item_cols = _columns("items")
    if "tracks_serial" not in item_cols:
        op.add_column("items", sa.Column("tracks_serial", sa.Boolean(), nullable=False, server_default=sa.false()))

    purchase_item_cols = _columns("purchase_items")
    if "serials_json" not in purchase_item_cols:
        op.add_column("purchase_items", sa.Column("serials_json", sa.Text(), nullable=True))

    movement_cols = _columns("stock_movements")
    movement_additions = []
    if "warehouse_id" not in movement_cols:
        movement_additions.append(sa.Column("warehouse_id", sa.Integer(), nullable=True))
    if "counterparty_warehouse_id" not in movement_cols:
        movement_additions.append(sa.Column("counterparty_warehouse_id", sa.Integer(), nullable=True))
    if movement_additions:
        with op.batch_alter_table("stock_movements") as batch:
            for column in movement_additions:
                batch.add_column(column)
            if "warehouse_id" not in movement_cols:
                batch.create_foreign_key("fk_stock_movement_warehouse", "warehouses", ["warehouse_id"], ["id"])
            if "counterparty_warehouse_id" not in movement_cols:
                batch.create_foreign_key("fk_stock_movement_counterparty_warehouse", "warehouses", ["counterparty_warehouse_id"], ["id"])
    if "warehouse_id" not in movement_cols:
        op.create_index("ix_stock_movements_warehouse_id", "stock_movements", ["warehouse_id"])
    # Best-effort warehouse backfill. Ambiguous historical rows remain nullable by design.
    op.execute(sa.text("""
        UPDATE stock_movements
        SET warehouse_id = (
            SELECT w.id FROM warehouses AS w
            WHERE w.branch_id = stock_movements.branch_id AND w.is_default = 1
            ORDER BY w.id LIMIT 1
        )
        WHERE warehouse_id IS NULL
    """))
    op.execute(sa.text("""
        UPDATE stock_movements
        SET counterparty_warehouse_id = (
            SELECT CASE
                WHEN stock_movements.warehouse_id = wt.from_warehouse_id THEN wt.to_warehouse_id
                ELSE wt.from_warehouse_id
            END
            FROM warehouse_transfers AS wt
            WHERE wt.number = stock_movements.reference
            LIMIT 1
        )
        WHERE counterparty_warehouse_id IS NULL
          AND reference IN (SELECT number FROM warehouse_transfers)
    """))

    line_cols = _columns("inventory_document_lines")
    line_additions = []
    if "entered_item_id" not in line_cols:
        line_additions.append(sa.Column("entered_item_id", sa.Integer(), nullable=True))
    if "entered_qty" not in line_cols:
        line_additions.append(sa.Column("entered_qty", sa.Float(), nullable=True))
    if "conversion_factor" not in line_cols:
        line_additions.append(sa.Column("conversion_factor", sa.Float(), nullable=False, server_default="1"))
    if line_additions:
        with op.batch_alter_table("inventory_document_lines") as batch:
            for column in line_additions:
                batch.add_column(column)
            if "entered_item_id" not in line_cols:
                batch.create_foreign_key("fk_inventory_document_line_entered_item", "items", ["entered_item_id"], ["id"])

    transfer_cols = _columns("warehouse_transfers")
    transfer_additions = []
    for name, column in (
        ("cancelled_at", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)),
        ("cancelled_by", sa.Column("cancelled_by", sa.Integer(), nullable=True)),
        ("cancellation_reason", sa.Column("cancellation_reason", sa.Text(), nullable=True)),
        ("reversal_journal_id", sa.Column("reversal_journal_id", sa.Integer(), nullable=True)),
        # Existing transfers have no exact batch lineage and are deliberately not reversible.
        ("can_cancel", sa.Column("can_cancel", sa.Boolean(), nullable=False, server_default=sa.false())),
    ):
        if name not in transfer_cols:
            transfer_additions.append(column)
    if transfer_additions:
        with op.batch_alter_table("warehouse_transfers") as batch:
            for column in transfer_additions:
                batch.add_column(column)
            if "cancelled_by" not in transfer_cols:
                batch.create_foreign_key("fk_warehouse_transfer_cancelled_by", "users", ["cancelled_by"], ["id"])
            if "reversal_journal_id" not in transfer_cols:
                batch.create_foreign_key("fk_warehouse_transfer_reversal_journal", "journals", ["reversal_journal_id"], ["id"])

    transfer_item_cols = _columns("warehouse_transfer_items")
    transfer_item_additions = []
    if "entered_item_id" not in transfer_item_cols:
        transfer_item_additions.append(sa.Column("entered_item_id", sa.Integer(), nullable=True))
    if "entered_qty" not in transfer_item_cols:
        transfer_item_additions.append(sa.Column("entered_qty", sa.Float(), nullable=True))
    if "conversion_factor" not in transfer_item_cols:
        transfer_item_additions.append(sa.Column("conversion_factor", sa.Float(), nullable=False, server_default="1"))
    if "serials_json" not in transfer_item_cols:
        transfer_item_additions.append(sa.Column("serials_json", sa.Text(), nullable=True))
    if transfer_item_additions:
        with op.batch_alter_table("warehouse_transfer_items") as batch:
            for column in transfer_item_additions:
                batch.add_column(column)
            if "entered_item_id" not in transfer_item_cols:
                batch.create_foreign_key("fk_warehouse_transfer_item_entered_item", "items", ["entered_item_id"], ["id"])

    if "warehouse_transfer_batch_allocations" not in tables:
        op.create_table(
            "warehouse_transfer_batch_allocations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("transfer_item_id", sa.Integer(), sa.ForeignKey("warehouse_transfer_items.id"), nullable=False),
            sa.Column("source_batch_id", sa.Integer(), sa.ForeignKey("stock_batches.id"), nullable=False),
            sa.Column("destination_batch_id", sa.Integer(), sa.ForeignKey("stock_batches.id"), nullable=False),
            sa.Column("qty", sa.Float(), nullable=False),
            sa.Column("unit_cost", sa.Float(), nullable=False, server_default="0"),
        )
        op.create_index("ix_transfer_batch_alloc_item", "warehouse_transfer_batch_allocations", ["transfer_item_id"])
        op.create_index("ix_transfer_batch_alloc_source", "warehouse_transfer_batch_allocations", ["source_batch_id"])
        op.create_index("ix_transfer_batch_alloc_destination", "warehouse_transfer_batch_allocations", ["destination_batch_id"])

    if "item_serials" not in tables:
        op.create_table(
            "item_serials",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
            sa.Column("serial", sa.String(length=255), nullable=False),
            sa.Column("normalized_serial", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="in_stock"),
            sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("normalized_serial", name="uq_item_serials_normalized"),
        )
        op.create_index("ix_item_serials_item_status", "item_serials", ["item_id", "status"])
        op.create_index("ix_item_serials_warehouse", "item_serials", ["warehouse_id"])

    if "item_serial_events" not in tables:
        op.create_table(
            "item_serial_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("serial_id", sa.Integer(), sa.ForeignKey("item_serials.id"), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("from_status", sa.String(length=30), nullable=True),
            sa.Column("to_status", sa.String(length=30), nullable=False),
            sa.Column("from_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
            sa.Column("to_warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True),
            sa.Column("reference_type", sa.String(length=50), nullable=False),
            sa.Column("reference_id", sa.Integer(), nullable=True),
            sa.Column("reference_number", sa.String(length=100), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_item_serial_events_serial_created", "item_serial_events", ["serial_id", "created_at"])
        op.create_index("ix_item_serial_events_reference", "item_serial_events", ["reference_type", "reference_id"])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "item_serial_events" in tables:
        op.drop_table("item_serial_events")
    if "item_serials" in tables:
        op.drop_table("item_serials")
    if "warehouse_transfer_batch_allocations" in tables:
        op.drop_table("warehouse_transfer_batch_allocations")
    transfer_item_cols = _columns("warehouse_transfer_items")
    with op.batch_alter_table("warehouse_transfer_items") as batch:
        if "entered_item_id" in transfer_item_cols:
            batch.drop_constraint("fk_warehouse_transfer_item_entered_item", type_="foreignkey")
        for name in ("serials_json", "conversion_factor", "entered_qty", "entered_item_id"):
            if name in transfer_item_cols:
                batch.drop_column(name)
    transfer_cols = _columns("warehouse_transfers")
    with op.batch_alter_table("warehouse_transfers") as batch:
        if "reversal_journal_id" in transfer_cols:
            batch.drop_constraint("fk_warehouse_transfer_reversal_journal", type_="foreignkey")
        if "cancelled_by" in transfer_cols:
            batch.drop_constraint("fk_warehouse_transfer_cancelled_by", type_="foreignkey")
        for name in ("can_cancel", "reversal_journal_id", "cancellation_reason", "cancelled_by", "cancelled_at"):
            if name in transfer_cols:
                batch.drop_column(name)
    line_cols = _columns("inventory_document_lines")
    with op.batch_alter_table("inventory_document_lines") as batch:
        if "entered_item_id" in line_cols:
            batch.drop_constraint("fk_inventory_document_line_entered_item", type_="foreignkey")
        for name in ("conversion_factor", "entered_qty", "entered_item_id"):
            if name in line_cols:
                batch.drop_column(name)
    movement_cols = _columns("stock_movements")
    if "warehouse_id" in movement_cols:
        op.drop_index("ix_stock_movements_warehouse_id", table_name="stock_movements")
    with op.batch_alter_table("stock_movements") as batch:
        if "counterparty_warehouse_id" in movement_cols:
            batch.drop_constraint("fk_stock_movement_counterparty_warehouse", type_="foreignkey")
            batch.drop_column("counterparty_warehouse_id")
        if "warehouse_id" in movement_cols:
            batch.drop_constraint("fk_stock_movement_warehouse", type_="foreignkey")
            batch.drop_column("warehouse_id")
    if "tracks_serial" in _columns("items"):
        op.drop_column("items", "tracks_serial")
    if "serials_json" in _columns("purchase_items"):
        op.drop_column("purchase_items", "serials_json")
