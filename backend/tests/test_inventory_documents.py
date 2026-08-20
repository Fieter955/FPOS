import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models
from app.services import inventory_documents as service
from app.services.inventory_fifo import add_batch, total_remaining


class InventoryDocumentTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.branch = models.Branch(code="INV", name="Cabang Persediaan")
        self.db.add(self.branch)
        self.db.flush()
        self.warehouse = models.Warehouse(
            code="INV-WH", name="Gudang Utama", branch_id=self.branch.id,
            is_default=True, is_active=True,
        )
        self.user = models.User(
            username="admin-inventory", full_name="Admin Persediaan",
            hashed_password="x", role="admin", is_active=True,
            branch_id=self.branch.id, active_branch_id=self.branch.id,
        )
        self.db.add_all([self.warehouse, self.user])
        for code, name, kind, subtype, normal in [
            ("1-1400", "Persediaan Barang", "asset", "current_asset", "debit"),
            ("3-1999", "Modal Transisi", "equity", "capital", "credit"),
            ("4-1300", "Pendapatan Lain", "revenue", "non_operating", "credit"),
            ("5-1200", "Beban Selisih", "expense", "cogs", "debit"),
            ("5-2700", "Beban Lain", "expense", "non_operating", "debit"),
        ]:
            self.db.add(models.Account(
                code=code, name=name, type=kind, subtype=subtype,
                normal_balance=normal, is_active=True,
            ))
        self.item = models.Item(code="SKU-1", name="Barang Uji", buy_price=10, stock=0, is_active=True)
        self.db.add(self.item)
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=self.warehouse.id, item_id=self.item.id, stock=0,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def payload(self, doc_type, **line):
        return SimpleNamespace(
            type=doc_type,
            date=service.local_date(),
            warehouse_id=self.warehouse.id,
            notes="Uji otomatis",
            surplus_account_id=None,
            shortage_account_id=None,
            lines=[SimpleNamespace(item_id=self.item.id, notes=None, **line)],
        )

    def stock(self):
        return service.warehouse_stock(self.db, self.warehouse.id, self.item.id)

    def test_item_in_creates_batch_movement_journal_and_exact_reversal(self):
        document = service.create_document(self.db, self.payload("item_in", qty=5), self.user)
        self.db.commit()
        self.assertEqual(self.stock(), 5)
        self.assertEqual(total_remaining(self.db, self.item.id, self.warehouse.id), 5)
        self.assertEqual(document.lines[0].total_cost, 50)
        self.assertIsNotNone(document.journal_id)

        service.cancel_document(self.db, document, "Salah input", self.user)
        self.db.commit()
        self.assertEqual(self.stock(), 0)
        self.assertEqual(total_remaining(self.db, self.item.id, self.warehouse.id), 0)
        self.assertEqual(document.status, "cancelled")
        self.assertIsNotNone(document.reversal_journal_id)

    def test_item_out_consumes_fifo_and_cancel_restores_same_batches(self):
        first = add_batch(
            self.db, item_id=self.item.id, warehouse_id=self.warehouse.id,
            qty=3, unit_cost=10, received_date=service.local_date(),
        )
        second = add_batch(
            self.db, item_id=self.item.id, warehouse_id=self.warehouse.id,
            qty=4, unit_cost=20, received_date=service.local_date(),
        )
        self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.item.id
        ).one().stock = 7
        self.item.stock = 7
        self.db.commit()

        document = service.create_document(self.db, self.payload("item_out", qty=5), self.user)
        self.db.commit()
        self.assertEqual(self.stock(), 2)
        self.assertEqual(document.lines[0].total_cost, 70)
        self.assertEqual(first.qty_remaining, 0)
        self.assertEqual(second.qty_remaining, 2)

        service.cancel_document(self.db, document, "Batal barang keluar", self.user)
        self.db.commit()
        self.assertEqual(self.stock(), 7)
        self.assertEqual(first.qty_remaining, 3)
        self.assertEqual(second.qty_remaining, 4)

    def test_opname_uses_physical_qty_and_rejects_stale_snapshot(self):
        token = service.snapshot_token(self.warehouse.id, self.item.id, 0)
        payload = self.payload("stock_opname", physical_qty=2, snapshot_token=token)
        service.create_document(self.db, payload, self.user)
        self.db.commit()
        self.assertEqual(self.stock(), 2)

        stale = self.payload("stock_opname", physical_qty=3, snapshot_token=token)
        with self.assertRaises(HTTPException) as ctx:
            service.create_document(self.db, stale, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.stock(), 2)

    def test_zero_difference_opname_is_audited_without_movement_or_journal(self):
        token = service.snapshot_token(self.warehouse.id, self.item.id, 0)
        before = self.db.query(func.count(models.StockMovement.id)).scalar()
        document = service.create_document(
            self.db,
            self.payload("stock_opname", physical_qty=0, snapshot_token=token),
            self.user,
        )
        self.db.commit()
        self.assertEqual(document.lines[0].qty_delta, 0)
        self.assertIsNone(document.journal_id)
        self.assertEqual(self.db.query(func.count(models.StockMovement.id)).scalar(), before)

    def test_opening_stock_is_one_time_only(self):
        service.create_document(self.db, self.payload("opening_stock", qty=4), self.user)
        self.db.commit()
        with self.assertRaises(HTTPException):
            service.create_document(self.db, self.payload("opening_stock", qty=1), self.user)
        self.db.rollback()
        self.assertEqual(self.stock(), 4)

    def test_multi_line_failure_rolls_back_the_entire_document(self):
        invalid = models.Item(
            code="SKU-VIRTUAL", name="Barang Turunan Uji", buy_price=5,
            stock=0, is_active=True, is_virtual_variant=True,
            parent_item_id=self.item.id, conversion_factor_to_parent=0.5,
        )
        self.db.add(invalid)
        self.db.flush()
        payload = SimpleNamespace(
            type="item_in", date=service.local_date(), warehouse_id=self.warehouse.id,
            notes="Harus rollback", surplus_account_id=None, shortage_account_id=None,
            lines=[
                SimpleNamespace(item_id=self.item.id, qty=2, physical_qty=None, snapshot_token=None, notes=None),
                SimpleNamespace(item_id=invalid.id, qty=1, physical_qty=None, snapshot_token=None, notes=None),
            ],
        )
        with self.assertRaises(HTTPException):
            service.create_document(self.db, payload, self.user)
        self.db.rollback()
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.db.query(models.InventoryDocument).count(), 0)
        self.assertEqual(total_remaining(self.db, self.item.id, self.warehouse.id), 0)

    def test_inbound_cancellation_is_blocked_after_its_batch_is_consumed(self):
        inbound = service.create_document(self.db, self.payload("item_in", qty=5), self.user)
        self.db.commit()
        service.create_document(self.db, self.payload("item_out", qty=1), self.user)
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            service.cancel_document(self.db, inbound, "Batal terlambat", self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.stock(), 4)


if __name__ == "__main__":
    unittest.main()
