import importlib.util
from pathlib import Path
import unittest

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


class InventorySerialMigrationTests(unittest.TestCase):
    def test_upgrade_from_inventory_head_adds_foundation(self):
        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        sa.Table("branches", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("items", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("warehouses", metadata, sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("branch_id", sa.Integer), sa.Column("is_default", sa.Boolean))
        sa.Table("accounts", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("journals", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("purchases", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("purchase_items", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("stock_batches", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("stock_movements", metadata,
                 sa.Column("id", sa.Integer, primary_key=True), sa.Column("branch_id", sa.Integer),
                 sa.Column("reference", sa.String(100)))
        sa.Table("inventory_document_lines", metadata,
                 sa.Column("id", sa.Integer, primary_key=True), sa.Column("item_id", sa.Integer))
        sa.Table("warehouse_transfers", metadata,
                 sa.Column("id", sa.Integer, primary_key=True), sa.Column("number", sa.String(50)),
                 sa.Column("from_warehouse_id", sa.Integer), sa.Column("to_warehouse_id", sa.Integer))
        sa.Table("warehouse_transfer_items", metadata,
                 sa.Column("id", sa.Integer, primary_key=True), sa.Column("item_id", sa.Integer))
        metadata.create_all(engine)

        migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "c9d0e1f2a3b4_inventory_transfer_serial_foundation.py"
        spec = importlib.util.spec_from_file_location("inventory_serial_migration", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection, opts={"render_as_batch": True}))
            module.upgrade()
            inspector = sa.inspect(connection)
            self.assertIn("tracks_serial", {c["name"] for c in inspector.get_columns("items")})
            self.assertIn("warehouse_id", {c["name"] for c in inspector.get_columns("stock_movements")})
            self.assertIn("can_cancel", {c["name"] for c in inspector.get_columns("warehouse_transfers")})
            self.assertIn("item_serials", inspector.get_table_names())
            self.assertIn("item_serial_events", inspector.get_table_names())
            self.assertIn("warehouse_transfer_batch_allocations", inspector.get_table_names())
            module.downgrade()
            inspector = sa.inspect(connection)
            self.assertNotIn("tracks_serial", {c["name"] for c in inspector.get_columns("items")})
            self.assertNotIn("item_serials", inspector.get_table_names())

    def test_return_audit_migration_adds_provenance_and_reversal_columns(self):
        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("journals", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("sale_items", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("sale_returns", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("sale_return_items", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("purchase_returns", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("purchase_return_items", metadata, sa.Column("id", sa.Integer, primary_key=True))
        metadata.create_all(engine)

        migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "d0e1f2a3b4c5_return_serial_audit.py"
        spec = importlib.util.spec_from_file_location("return_serial_audit_migration", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection, opts={"render_as_batch": True}))
            module.upgrade()
            inspector = sa.inspect(connection)
            self.assertIn("serials_json", {c["name"] for c in inspector.get_columns("sale_items")})
            self.assertIn("carrying_cost", {c["name"] for c in inspector.get_columns("sale_return_items")})
            self.assertIn("status", {c["name"] for c in inspector.get_columns("sale_returns")})
            self.assertIn("reversal_journal_id", {c["name"] for c in inspector.get_columns("purchase_returns")})
            module.downgrade()
            inspector = sa.inspect(connection)
            self.assertNotIn("serials_json", {c["name"] for c in inspector.get_columns("sale_items")})
            self.assertNotIn("status", {c["name"] for c in inspector.get_columns("sale_returns")})


if __name__ == "__main__":
    unittest.main()
