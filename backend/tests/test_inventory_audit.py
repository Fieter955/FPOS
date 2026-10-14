import sqlite3
import unittest

from inventory_audit import audit_inventory


class InventoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE warehouses (id INTEGER PRIMARY KEY, branch_id INTEGER);
            CREATE TABLE items (id INTEGER PRIMARY KEY, code TEXT, name TEXT, stock REAL);
            CREATE TABLE warehouse_stocks (
                id INTEGER PRIMARY KEY, warehouse_id INTEGER, item_id INTEGER, stock REAL
            );
            CREATE TABLE stock_batches (
                id INTEGER PRIMARY KEY, warehouse_id INTEGER, item_id INTEGER,
                qty_received REAL, qty_remaining REAL, unit_cost REAL
            );
            CREATE TABLE stock_movements (
                id INTEGER PRIMARY KEY, date TEXT, branch_id INTEGER, warehouse_id INTEGER,
                item_id INTEGER, type TEXT, qty REAL, qty_before REAL, qty_after REAL,
                reference TEXT
            );
            CREATE TABLE accounts (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE journals (id INTEGER PRIMARY KEY, branch_id INTEGER);
            CREATE TABLE journal_entry_lines (
                id INTEGER PRIMARY KEY, journal_id INTEGER,
                debit_account_id INTEGER, credit_account_id INTEGER, amount REAL
            );
            CREATE TABLE inventory_documents (
                id INTEGER PRIMARY KEY, number TEXT, type TEXT, date TEXT, branch_id INTEGER,
                warehouse_id INTEGER, status TEXT, journal_id INTEGER, reversal_journal_id INTEGER
            );
            CREATE TABLE inventory_document_lines (
                id INTEGER PRIMARY KEY, document_id INTEGER, qty_delta REAL, total_cost REAL
            );
            INSERT INTO branches VALUES (1, 'Pusat');
            INSERT INTO warehouses VALUES (1, 1);
            INSERT INTO items VALUES (1, 'SKU', 'Barang', 2);
            INSERT INTO warehouse_stocks VALUES (1, 1, 1, 2);
            INSERT INTO stock_batches VALUES (1, 1, 1, 2, 2, 10);
            INSERT INTO accounts VALUES (1, '1-1400');
            INSERT INTO journals VALUES (1, 1);
            INSERT INTO journal_entry_lines VALUES (1, 1, 1, NULL, 20);
        """)

    def tearDown(self):
        self.db.close()

    def test_balanced_inventory_has_no_findings(self):
        report = audit_inventory(self.db)
        self.assertEqual(report["database_integrity"], "ok")
        self.assertEqual(report["finding_count"], 0)

    def test_detects_quantity_value_cache_movement_and_document_anomalies(self):
        self.db.executescript("""
            UPDATE warehouse_stocks SET stock = 3;
            UPDATE items SET stock = 4;
            INSERT INTO stock_movements VALUES
                (1, '2026-09-14', 1, 1, 1, 'item_in', 0, 2, 2, 'BAD-MOVE');
            INSERT INTO inventory_documents VALUES
                (1, 'OPN-BAD', 'stock_opname', '2026-09-14', 1, 1, 'posted', NULL, NULL);
            INSERT INTO inventory_document_lines VALUES (1, 1, 1, 0);
        """)
        report = audit_inventory(self.db)
        self.assertEqual(len(report["checks"]["fifo_quantity_drift"]), 1)
        self.assertEqual(len(report["checks"]["inventory_value_drift"]), 0)
        self.assertEqual(len(report["checks"]["central_stock_cache_drift"]), 1)
        self.assertEqual(len(report["checks"]["invalid_movements"]), 1)
        self.assertEqual(len(report["checks"]["zero_cost_inventory_documents"]), 1)
        self.assertGreaterEqual(report["finding_count"], 4)


if __name__ == "__main__":
    unittest.main()
