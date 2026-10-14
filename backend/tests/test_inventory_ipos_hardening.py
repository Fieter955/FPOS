import json
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
from app.routes.serials import transaction_serial_options
from app.routes.returns import (
    ReturnCancel,
    cancel_purchase_return,
    cancel_sale_return,
    get_purchase_history_items,
    get_sale_history_items,
)


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

    def test_transaction_serial_options_resolve_virtual_item_and_scope_warehouse(self):
        self.base.tracks_serial = True
        receive_serials(self.db, item=self.base, qty=1, serial_values=["OPT-1"],
                        warehouse=self.warehouse, reference_type="test", reference_id=3,
                        reference_number="T-3", user_id=self.user.id)
        receive_serials(self.db, item=self.base, qty=1, serial_values=["OPT-2"],
                        warehouse=self.second, reference_type="test", reference_id=4,
                        reference_number="T-4", user_id=self.user.id)
        self.db.commit()

        result = transaction_serial_options(
            purpose="sale", item_id=self.box.id, warehouse_id=self.warehouse.id,
            search=None, skip=0, limit=100, db=self.db, user=self.user,
        )

        self.assertTrue(result["tracking_required"])
        self.assertEqual(result["serial_item_id"], self.base.id)
        self.assertEqual(result["quantity_factor"], 10)
        self.assertEqual([row["serial"] for row in result["items"]], ["OPT-1"])

    def test_sale_return_serial_options_are_limited_to_selected_invoice_line(self):
        self.base.tracks_serial = True
        receive_serials(self.db, item=self.base, qty=2, serial_values=["SALE-A", "SALE-B"],
                        warehouse=self.warehouse, reference_type="purchase", reference_id=1,
                        reference_number="P-1", user_id=self.user.id)
        sale_a = models.Sale(number="S-A", date=inventory_documents.local_date(), branch_id=self.branch.id,
                             subtotal=10, total=10, paid=10, status="paid", created_by=self.user.id)
        sale_b = models.Sale(number="S-B", date=inventory_documents.local_date(), branch_id=self.branch.id,
                             subtotal=10, total=10, paid=10, status="paid", created_by=self.user.id)
        self.db.add_all([sale_a, sale_b])
        self.db.flush()
        line_a = models.SaleItem(sale_id=sale_a.id, item_id=self.base.id, qty=1, buy_price=10,
                                 sell_price=10, total=10, serials_json=json.dumps(["SALE-A"]))
        line_b = models.SaleItem(sale_id=sale_b.id, item_id=self.base.id, qty=1, buy_price=10,
                                 sell_price=10, total=10, serials_json=json.dumps(["SALE-B"]))
        self.db.add_all([line_a, line_b])
        self.db.flush()
        transition_serials(self.db, item=self.base, qty=1, serial_values=["SALE-A"],
                           from_statuses={"in_stock"}, to_status="sold",
                           from_warehouse_id=self.warehouse.id, to_warehouse=None,
                           reference_type="sale", reference_id=sale_a.id, reference_number=sale_a.number,
                           user_id=self.user.id, event_type="sold")
        transition_serials(self.db, item=self.base, qty=1, serial_values=["SALE-B"],
                           from_statuses={"in_stock"}, to_status="sold",
                           from_warehouse_id=self.warehouse.id, to_warehouse=None,
                           reference_type="sale", reference_id=sale_b.id, reference_number=sale_b.number,
                           user_id=self.user.id, event_type="sold")
        self.db.commit()

        result = transaction_serial_options(
            purpose="sale_return", item_id=self.base.id, source_line_id=line_a.id,
            warehouse_id=None, search=None, skip=0, limit=100, db=self.db, user=self.user,
        )
        self.assertEqual([row["serial"] for row in result["items"]], ["SALE-A"])
        history = get_sale_history_items(customer_id=None, start_date=None, end_date=None,
                                         status=None, db=self.db, current_user=self.user)
        selected_history = next(row for row in history if row["sale_item_id"] == line_a.id)
        self.assertTrue(selected_history["item"]["tracks_serial"])

        with self.assertRaises(HTTPException) as missing_source:
            transaction_serial_options(
                purpose="sale_return", item_id=self.base.id, source_line_id=None,
                warehouse_id=None, search=None, skip=0, limit=100, db=self.db, user=self.user,
            )
        self.assertEqual(missing_source.exception.status_code, 400)

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

    def test_sale_return_cancellation_reverses_stock_deposit_journal_and_quota(self):
        customer = models.Customer(code="C-REV", name="Pelanggan Reversal", deposit_balance=100)
        self.db.add(customer)
        self.db.flush()
        sale = models.Sale(number="S-REV", date=inventory_documents.local_date(),
                           customer_id=customer.id, branch_id=self.branch.id, subtotal=100,
                           total=100, paid=100, status="paid", created_by=self.user.id)
        self.db.add(sale)
        self.db.flush()
        sale_line = models.SaleItem(sale_id=sale.id, item_id=self.base.id, qty=1,
                                    buy_price=10, sell_price=100, total=100)
        self.db.add(sale_line)
        self.db.flush()
        return_doc = models.SaleReturn(number="RS-REV", date=inventory_documents.local_date(),
                                       sale_id=sale.id, branch_id=self.branch.id, total=100,
                                       status="posted", created_by=self.user.id)
        self.db.add(return_doc)
        self.db.flush()
        self.db.add(models.SaleReturnItem(return_id=return_doc.id, sale_item_id=sale_line.id,
                                          item_id=self.base.id, qty=1, price=100, total=100,
                                          condition="good", carrying_cost=10))
        warehouse_stock = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.base.id,
        ).one()
        warehouse_stock.stock = 1
        add_batch(self.db, item_id=self.base.id, warehouse_id=self.warehouse.id,
                  qty=1, unit_cost=10, received_date=inventory_documents.local_date())
        self.db.add(models.StockMovement(
            date=inventory_documents.local_date(), item_id=self.base.id,
            branch_id=self.branch.id, warehouse_id=self.warehouse.id, type="in", qty=1,
            qty_before=0, qty_after=1, reference=return_doc.number,
        ))
        accounts = self.db.query(models.Account).order_by(models.Account.id).limit(2).all()
        journal = models.Journal(number="JU-REV", date=inventory_documents.local_date(),
                                 description="Retur", branch_id=self.branch.id,
                                 reference=return_doc.number, source="auto", created_by=self.user.id)
        self.db.add(journal)
        self.db.flush()
        self.db.add_all([
            models.JournalEntryLine(journal_id=journal.id, debit_account_id=accounts[0].id,
                                    amount=100, description="Retur"),
            models.JournalEntryLine(journal_id=journal.id, credit_account_id=accounts[1].id,
                                    amount=100, description="Retur"),
        ])
        self.db.commit()

        cancel_sale_return(return_doc.id, ReturnCancel(reason="Salah serial"), self.db, self.user)
        self.assertEqual(return_doc.status, "cancelled")
        self.assertEqual(warehouse_stock.stock, 0)
        self.assertEqual(total_remaining(self.db, self.base.id, self.warehouse.id), 0)
        self.assertEqual(customer.deposit_balance, 0)
        self.assertIsNotNone(return_doc.reversal_journal_id)
        history = get_sale_history_items(customer_id=customer.id, start_date=None, end_date=None,
                                         status=None, db=self.db, current_user=self.user)
        self.assertEqual(history[0]["qty_available"], 1)

    def test_purchase_return_cancellation_restores_stock_fifo_deposit_and_quota(self):
        supplier = models.Supplier(code="SUP-REV", name="Supplier Reversal", deposit_balance=100)
        self.db.add(supplier)
        self.db.flush()
        purchase = models.Purchase(number="P-REV", date=inventory_documents.local_date(),
                                   supplier_id=supplier.id, branch_id=self.branch.id, subtotal=100,
                                   total=100, paid=0, status="unpaid", created_by=self.user.id)
        self.db.add(purchase)
        self.db.flush()
        purchase_line = models.PurchaseItem(purchase_id=purchase.id, item_id=self.base.id,
                                            qty=1, qty_ordered=1, qty_received=1,
                                            buy_price=100, total=100)
        self.db.add(purchase_line)
        self.db.flush()
        return_doc = models.PurchaseReturn(number="RP-REV", date=inventory_documents.local_date(),
                                           purchase_id=purchase.id, branch_id=self.branch.id,
                                           total=100, total_carrying=10, status="posted",
                                           created_by=self.user.id)
        self.db.add(return_doc)
        self.db.flush()
        self.db.add(models.PurchaseReturnItem(
            return_id=return_doc.id, purchase_item_id=purchase_line.id,
            item_id=self.base.id, qty=1, price=100, total=100, carrying_cost=10,
        ))
        accounts = self.db.query(models.Account).order_by(models.Account.id).limit(2).all()
        journal = models.Journal(number="JU-RP-REV", date=inventory_documents.local_date(),
                                 description="Retur beli", branch_id=self.branch.id,
                                 reference=return_doc.number, source="auto", created_by=self.user.id)
        self.db.add(journal)
        self.db.flush()
        self.db.add_all([
            models.JournalEntryLine(journal_id=journal.id, debit_account_id=accounts[0].id,
                                    amount=100, description="Retur beli"),
            models.JournalEntryLine(journal_id=journal.id, credit_account_id=accounts[1].id,
                                    amount=100, description="Retur beli"),
        ])
        self.db.commit()

        cancel_purchase_return(return_doc.id, ReturnCancel(reason="Salah barang"), self.db, self.user)
        warehouse_stock = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.base.id,
        ).one()
        self.assertEqual(return_doc.status, "cancelled")
        self.assertEqual(warehouse_stock.stock, 1)
        self.assertEqual(total_remaining(self.db, self.base.id, self.warehouse.id), 1)
        self.assertEqual(supplier.deposit_balance, 0)
        self.assertIsNotNone(return_doc.reversal_journal_id)
        history = get_purchase_history_items(supplier_id=supplier.id, start_date=None, end_date=None,
                                             status=None, db=self.db, current_user=self.user)
        self.assertEqual(history[0]["qty_available"], 1)

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
