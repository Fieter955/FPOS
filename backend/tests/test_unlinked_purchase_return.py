import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes import returns


class UnlinkedPurchaseReturnTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        branch = models.Branch(id=1, code="PST", name="Pusat")
        warehouse = models.Warehouse(code="WH", name="Gudang", branch_id=1, is_default=True, is_active=True)
        self.user = models.User(username="admin-retur", full_name="Admin", hashed_password="x",
                                role="admin", is_active=True, branch_id=1, active_branch_id=1)
        self.supplier = models.Supplier(code="SUP-RET", name="Supplier Retur", deposit_balance=0)
        self.item = models.Item(code="RET-1", name="Barang Retur", buy_price=20, stock=5, is_active=True)
        self.db.add_all([branch, warehouse, self.user, self.supplier, self.item])
        self.db.flush()
        self.warehouse = warehouse
        self.db.add(models.WarehouseStock(warehouse_id=warehouse.id, item_id=self.item.id, stock=5))
        self.db.add_all([
            models.StockBatch(item_id=self.item.id, warehouse_id=warehouse.id, qty_received=2,
                              qty_remaining=2, unit_cost=10, received_date=returns.get_local_date()),
            models.StockBatch(item_id=self.item.id, warehouse_id=warehouse.id, qty_received=3,
                              qty_remaining=3, unit_cost=20, received_date=returns.get_local_date()),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @patch("app.services.journal_service.create_purchase_return_journal")
    @patch("app.routes.accounting.pastikan_akun_ada")
    def test_unlinked_return_consumes_fifo_and_credits_supplier(self, _accounts, journal):
        result = returns.create_purchase_return({
            "return_mode": "unlinked",
            "supplier_id": self.supplier.id,
            "reason": "Barang rusak dari data sebelum migrasi",
            "evidence_reference": "KLAIM-SUP-17",
            "items": [{"item_id": self.item.id, "qty": 3, "price": 15, "serials": []}],
        }, self.db, self.user)

        document = self.db.query(models.PurchaseReturn).get(result["id"])
        batches = self.db.query(models.StockBatch).order_by(models.StockBatch.id).all()
        stock = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.item.id
        ).one()
        self.assertIsNone(document.purchase_id)
        self.assertEqual(document.return_mode, "unlinked")
        self.assertEqual(document.evidence_reference, "KLAIM-SUP-17")
        self.assertEqual(document.total, 45)
        self.assertEqual(document.total_carrying, 40)
        self.assertEqual(document.selisih, 5)
        self.assertEqual(self.db.query(models.Supplier).get(self.supplier.id).deposit_balance, 45)
        self.assertEqual(stock.stock, 2)
        self.assertEqual([batch.qty_remaining for batch in batches], [0, 2])
        journal.assert_called_once()

    @patch("app.services.journal_service.create_purchase_return_journal")
    @patch("app.routes.accounting.pastikan_akun_ada")
    def test_unlinked_return_requires_reason_and_evidence(self, _accounts, _journal):
        with self.assertRaisesRegex(Exception, "Alasan"):
            returns.create_purchase_return({
                "return_mode": "unlinked", "supplier_id": self.supplier.id,
                "evidence_reference": "X", "items": [{"item_id": self.item.id, "qty": 1, "price": 10}],
            }, self.db, self.user)

    def test_unlinked_stock_options_include_good_and_open_damaged_stock(self):
        quarantine = models.InventoryQuarantine(
            number="QRT-RET-1", date=returns.get_local_date(), branch_id=1,
            warehouse_id=self.warehouse.id, item_id=self.item.id,
            qty=3, qty_open=2, unit_cost=20, total_cost=60,
            source_type="warehouse_check", status="partial", created_by=self.user.id,
        )
        other_branch = models.Branch(code="OTH", name="Cabang Lain")
        self.db.add_all([quarantine, other_branch])
        self.db.flush()
        other_warehouse = models.Warehouse(
            code="OTH-WH", name="Gudang Lain", branch_id=other_branch.id,
            is_default=True, is_active=True,
        )
        self.db.add(other_warehouse)
        self.db.flush()
        self.db.add(models.InventoryQuarantine(
            number="QRT-OTHER", date=returns.get_local_date(), branch_id=other_branch.id,
            warehouse_id=other_warehouse.id, item_id=self.item.id,
            qty=9, qty_open=9, unit_cost=20,
            total_cost=180, source_type="warehouse_check", status="open",
            created_by=self.user.id,
        ))
        self.db.commit()

        result = returns.get_unlinked_stock_options(self.item.id, self.db, self.user)

        self.assertEqual(result["good"]["warehouse_id"], self.warehouse.id)
        self.assertEqual(result["good"]["qty_available"], 5)
        self.assertEqual(len(result["damaged_sources"]), 1)
        self.assertEqual(result["damaged_sources"][0]["source_type"], "inventory_quarantine")
        self.assertEqual(result["damaged_sources"][0]["source_item_id"], quarantine.id)
        self.assertEqual(result["damaged_sources"][0]["qty_available"], 2)

    @patch("app.services.journal_service.create_purchase_return_journal")
    @patch("app.routes.accounting.pastikan_akun_ada")
    def test_linked_damaged_return_records_quarantine_movement(self, _accounts, journal):
        purchase = models.Purchase(
            number="PB-QRT-1", date=returns.get_local_date(),
            supplier_id=self.supplier.id, branch_id=1, subtotal=40,
            total=40, paid=40, status="paid", created_by=self.user.id,
        )
        self.db.add(purchase)
        self.db.flush()
        purchase_line = models.PurchaseItem(
            purchase_id=purchase.id, item_id=self.item.id, qty=2,
            qty_ordered=2, qty_received=2, buy_price=20, total=40,
        )
        quarantine = models.InventoryQuarantine(
            number="QRT-LINKED", date=returns.get_local_date(), branch_id=1,
            warehouse_id=self.warehouse.id, item_id=self.item.id,
            qty=2, qty_open=2, unit_cost=13, total_cost=26,
            source_type="warehouse_check", status="open", created_by=self.user.id,
            supplier_id=self.supplier.id,
        )
        self.db.add_all([purchase_line, quarantine])
        self.db.commit()

        returns.create_purchase_return({
            "return_mode": "invoice", "purchase_id": purchase.id,
            "reason": "Rusak", "items": [{
                "item_id": self.item.id, "purchase_item_id": purchase_line.id,
                "qty": 1, "price": 20,
                "damaged_source_type": "inventory_quarantine",
                "damaged_source_item_id": quarantine.id, "serials": [],
            }],
        }, self.db, self.user)

        self.db.refresh(quarantine)
        movement = self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_supplier_return",
        ).one()
        self.assertEqual(quarantine.qty_open, 1)
        self.assertEqual(movement.reference.startswith("RP"), True)
        self.assertEqual((movement.qty_before, movement.qty_after), (5, 5))
        journal.assert_called_once()

    @patch("app.services.journal_service.create_purchase_return_journal")
    @patch("app.routes.accounting.pastikan_akun_ada")
    def test_unlinked_damaged_return_uses_selected_quarantine_source(self, _accounts, journal):
        quarantine = models.InventoryQuarantine(
            number="QRT-RET-2", date=returns.get_local_date(), branch_id=1,
            warehouse_id=self.warehouse.id, item_id=self.item.id,
            qty=2, qty_open=2, unit_cost=13, total_cost=26,
            source_type="warehouse_check", status="open", created_by=self.user.id,
        )
        self.db.add(quarantine)
        self.db.commit()

        result = returns.create_purchase_return({
            "return_mode": "unlinked", "supplier_id": self.supplier.id,
            "reason": "Rusak", "evidence_reference": "KLAIM-RUSAK-1",
            "items": [{
                "item_id": self.item.id, "qty": 1, "price": 15,
                "damaged_source_type": "inventory_quarantine",
                "damaged_source_item_id": quarantine.id, "serials": [],
            }],
        }, self.db, self.user)

        self.db.refresh(quarantine)
        line = self.db.query(models.PurchaseReturnItem).filter_by(
            return_id=result["id"]
        ).one()
        stock = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.item.id
        ).one()
        self.assertEqual(quarantine.qty_open, 1)
        self.assertEqual(quarantine.status, "partial")
        self.assertEqual(line.inventory_quarantine_id, quarantine.id)
        self.assertEqual(line.carrying_cost, 13)
        self.assertEqual(stock.stock, 5)
        movement = self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_supplier_return",
        ).one()
        self.assertEqual(movement.direction, "neutral")
        self.assertEqual((movement.qty_before, movement.qty_after), (5, 5))
        self.assertEqual(movement.source_id, quarantine.id)
        journal.assert_called_once()

    @patch("app.routes.returns._reverse_return_journal")
    @patch("app.services.journal_service.create_purchase_return_journal")
    @patch("app.routes.accounting.pastikan_akun_ada")
    def test_cancelling_quarantine_return_records_neutral_reversal(
        self, _accounts, _journal, reverse_journal,
    ):
        reverse_journal.return_value = type("Journal", (), {"id": 99})()
        quarantine = models.InventoryQuarantine(
            number="QRT-RET-CANCEL", date=returns.get_local_date(), branch_id=1,
            warehouse_id=self.warehouse.id, item_id=self.item.id,
            qty=2, qty_open=2, unit_cost=13, total_cost=26,
            source_type="warehouse_check", status="open", created_by=self.user.id,
        )
        self.db.add(quarantine)
        self.db.commit()
        created = returns.create_purchase_return({
            "return_mode": "unlinked", "supplier_id": self.supplier.id,
            "reason": "Rusak", "evidence_reference": "KLAIM-BATAL-1",
            "items": [{
                "item_id": self.item.id, "qty": 1, "price": 15,
                "damaged_source_type": "inventory_quarantine",
                "damaged_source_item_id": quarantine.id, "serials": [],
            }],
        }, self.db, self.user)

        returns.cancel_purchase_return(
            created["id"], returns.ReturnCancel(reason="Salah retur"),
            self.db, self.user,
        )

        self.db.refresh(quarantine)
        reversal = self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_supplier_return_reversal",
        ).one()
        self.assertEqual(quarantine.qty_open, 2)
        self.assertEqual(quarantine.status, "open")
        self.assertEqual(reversal.direction, "neutral")
        self.assertEqual((reversal.qty_before, reversal.qty_after), (5, 5))


if __name__ == "__main__":
    unittest.main()
