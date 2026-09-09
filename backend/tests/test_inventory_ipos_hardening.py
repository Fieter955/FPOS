import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.services import inventory_documents
from app.services.serials import normalize_serial, receive_serials, transition_serials
from app.services.inventory_fifo import add_batch, total_remaining
from app.routes.warehouse import TransferCancel, TransferCreate, TransferItemIn, cancel_transfer, create_transfer_secure
from app.routes.items import _import_items_sync


class InventoryIposHardeningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.branch = models.Branch(code="HARD", name="Hardening")
        self.db.add(self.branch)
        self.db.flush()
        self.warehouse = models.Warehouse(code="HARD-WH", name="Utama", branch_id=self.branch.id,
                                          is_active=True, is_default=True)
        self.second = models.Warehouse(code="HARD-WH2", name="Dua", branch_id=self.branch.id,
                                       is_active=True, is_default=False)
        self.user = models.User(username="hard-admin", hashed_password="x", role="admin",
                                is_active=True, branch_id=self.branch.id, active_branch_id=self.branch.id)
        self.db.add_all([self.warehouse, self.second, self.user])
        for code, name, kind, subtype, normal in [
            ("1-1400", "Persediaan", "asset", "current_asset", "debit"),
            ("3-1999", "Modal", "equity", "capital", "credit"),
            ("4-1300", "Pendapatan", "revenue", "other", "credit"),
            ("5-1200", "Selisih", "expense", "cogs", "debit"),
            ("5-2700", "Beban", "expense", "other", "debit"),
        ]:
            self.db.add(models.Account(code=code, name=name, type=kind, subtype=subtype,
                                       normal_balance=normal, is_active=True))
        self.base = models.Item(code="BASE", name="Unit", buy_price=10, stock=0, is_active=True)
        self.db.add(self.base)
        self.db.flush()
        self.box = models.Item(code="BOX", name="Box", buy_price=100, stock=0, is_active=True,
                               parent_item_id=self.base.id, conversion_factor_to_parent=10,
                               is_virtual_variant=True)
        self.db.add(self.box)
        self.db.add_all([
            models.WarehouseStock(warehouse_id=self.warehouse.id, item_id=self.base.id, stock=0),
            models.WarehouseStock(warehouse_id=self.second.id, item_id=self.base.id, stock=0),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_multiuom_inventory_document_posts_base_quantity_and_metadata(self):
        payload = SimpleNamespace(
            type="opening_stock", date=inventory_documents.local_date(),
            warehouse_id=self.warehouse.id, notes="box opening", surplus_account_id=None,
            shortage_account_id=None,
            lines=[SimpleNamespace(item_id=self.box.id, qty=2, unit_cost=100, notes=None, serials=[])],
        )
        document = inventory_documents.create_document(self.db, payload, self.user)
        self.db.commit()
        line = document.lines[0]
        self.assertEqual(line.item_id, self.base.id)
        self.assertEqual(line.entered_item_id, self.box.id)
        self.assertEqual(line.entered_qty, 2)
        self.assertEqual(line.conversion_factor, 10)
        self.assertEqual(line.qty_delta, 20)
        self.assertEqual(inventory_documents.warehouse_stock(self.db, self.warehouse.id, self.base.id), 20)

    def test_serial_is_nfkc_normalized_global_and_tracks_transitions(self):
        self.base.tracks_serial = True
        receive_serials(self.db, item=self.base, qty=1, serial_values=["  ａｂ-01  "],
                        warehouse=self.warehouse, reference_type="test", reference_id=1,
                        reference_number="T-1", user_id=self.user.id)
        self.db.flush()
        self.db.commit()
        self.assertEqual(normalize_serial("ab-01"), "AB-01")
        with self.assertRaises(HTTPException) as duplicate:
            receive_serials(self.db, item=self.base, qty=1, serial_values=["AB-01"],
                            warehouse=self.second, reference_type="test", reference_id=2,
                            reference_number="T-2", user_id=self.user.id)
        self.assertEqual(duplicate.exception.status_code, 409)
        self.db.rollback()
        record = self.db.query(models.ItemSerial).filter_by(normalized_serial="AB-01").one()
        transition_serials(self.db, item=self.base, qty=1, serial_values=["ab-01"],
                           from_statuses={"in_stock"}, to_status="sold",
                           from_warehouse_id=self.warehouse.id, to_warehouse=None,
                           reference_type="sale", reference_id=1, reference_number="S-1",
                           user_id=self.user.id, event_type="sold")
        self.db.commit()
        self.assertEqual(record.status, "sold")
        self.assertEqual(len(record.events), 2)

    def test_serial_quantity_must_match_whole_base_units(self):
        self.base.tracks_serial = True
        with self.assertRaises(HTTPException):
            receive_serials(self.db, item=self.base, qty=1.5, serial_values=["A", "B"],
                            warehouse=self.warehouse, reference_type="test", reference_id=1,
                            reference_number="T", user_id=self.user.id)

    def test_transfer_records_exact_lineage_and_can_reverse_if_unconsumed(self):
        stock = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.base.id
        ).one()
        stock.stock = 5
        source = add_batch(self.db, item_id=self.base.id, warehouse_id=self.warehouse.id,
                           qty=5, unit_cost=10, received_date=inventory_documents.local_date())
        self.db.commit()
        result = create_transfer_secure(
            TransferCreate(date=inventory_documents.local_date(), from_warehouse_id=self.warehouse.id,
                           to_warehouse_id=self.second.id, items=[TransferItemIn(item_id=self.base.id, qty=3)]),
            self.db, self.user,
        )
        transfer = self.db.query(models.WarehouseTransfer).get(result["id"])
        self.assertTrue(transfer.can_cancel)
        self.assertEqual(len(transfer.items[0].batch_allocations), 1)
        self.assertEqual(source.qty_remaining, 2)
        self.assertEqual(total_remaining(self.db, self.base.id, self.second.id), 3)

        cancel_transfer(transfer.id, TransferCancel(reason="Salah gudang"), self.db, self.user)
        self.assertEqual(transfer.status, "cancelled")
        self.assertEqual(source.qty_remaining, 5)
        self.assertEqual(total_remaining(self.db, self.base.id, self.second.id), 0)

    def test_import_positive_stock_posts_one_opening_document_and_journal(self):
        csv_data = (
            "KODEITEM,NAMAITEM,HARGAPOKOK,HARGAJUAL,STOK,STOKMIN\n"
            "IMP-1,Barang Import,12,20,3,1\n"
            "IMP-2,Barang Import Dua,5,9,2,0\n"
        ).encode("utf-8")
        result = _import_items_sync(csv_data, "opening.csv", self.db, self.user)
        self.assertIsNotNone(result["opening_document_id"])
        document = self.db.query(models.InventoryDocument).get(result["opening_document_id"])
        self.assertEqual(len(document.lines), 2)
        self.assertIsNotNone(document.journal_id)
        self.assertEqual(sum(line.total_cost for line in document.lines), 46)
        self.assertEqual(self.db.query(models.StockMovement).filter_by(reference=document.number).count(), 2)

    def test_invalid_import_rolls_back_entire_file(self):
        before = self.db.query(models.Item).count()
        csv_data = (
            "KODEITEM,NAMAITEM,HARGAPOKOK,HARGAJUAL,STOK,STOKMIN\n"
            "OK-ROW,Valid,1,2,0,0\n"
            "BAD-ROW,Tidak Valid,1,2,-1,0\n"
        ).encode("utf-8")
        with self.assertRaises(HTTPException) as caught:
            _import_items_sync(csv_data, "invalid.csv", self.db, self.user)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.db.query(models.Item).count(), before)

    def test_transfer_rejects_origin_outside_active_branch(self):
        other_branch = models.Branch(code="OTHER", name="Cabang Lain")
        self.db.add(other_branch)
        self.db.flush()
        other_a = models.Warehouse(code="OTHER-A", name="Lain A", branch_id=other_branch.id, is_active=True)
        other_b = models.Warehouse(code="OTHER-B", name="Lain B", branch_id=other_branch.id, is_active=True)
        self.db.add_all([other_a, other_b])
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            create_transfer_secure(
                TransferCreate(date=inventory_documents.local_date(), from_warehouse_id=other_a.id,
                               to_warehouse_id=other_b.id,
                               items=[TransferItemIn(item_id=self.base.id, qty=1)]),
                self.db, self.user,
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_transfer_rejects_closed_accounting_period(self):
        today = inventory_documents.local_date()
        self.db.add(models.BookClose(branch_id=self.branch.id, close_type="month",
                                     period_start=today.replace(day=1), period_end=today,
                                     is_active=True, created_by=self.user.id))
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            create_transfer_secure(
                TransferCreate(date=today, from_warehouse_id=self.warehouse.id,
                               to_warehouse_id=self.second.id,
                               items=[TransferItemIn(item_id=self.base.id, qty=1)]),
                self.db, self.user,
            )
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
