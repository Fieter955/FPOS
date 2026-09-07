import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.routes.purchases import receive_branch_request
from app.routes.returns import create_branch_return_to_center, receive_branch_return_at_center
from app.services.purchase_flow import create_fulfillment_shipment, refresh_source_request_status


class BranchPoFulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.pusat = models.Branch(id=1, code="PUSAT", name="Toko Utama", status="Toko Utama")
        self.cabang = models.Branch(id=2, code="CBG", name="Cabang Uji")
        self.admin_pusat = models.User(
            username="pusat-po", full_name="Pusat", hashed_password="x", role="admin",
            branch_id=1, active_branch_id=1, is_active=True,
        )
        self.admin_cabang = models.User(
            username="cabang-po", full_name="Cabang", hashed_password="x", role="admin",
            branch_id=2, active_branch_id=2, is_active=True,
        )
        self.supplier = models.Supplier(code="SUP", name="Supplier A")
        self.item = models.Item(code="SKU-PO", name="Barang PO", buy_price=100, stock=0, is_active=True)
        self.db.add_all([self.pusat, self.cabang, self.admin_pusat, self.admin_cabang, self.supplier, self.item])
        self.db.flush()
        self.wh_pusat = models.Warehouse(code="WH-P", name="Gudang Pusat", branch_id=1, is_default=True, is_active=True)
        self.wh_cabang = models.Warehouse(code="WH-C", name="Gudang Cabang", branch_id=2, is_default=True, is_active=True)
        self.db.add_all([self.wh_pusat, self.wh_cabang])
        for code, name, kind, subtype, normal in [
            ("1-1400", "Persediaan", "asset", "inventory", "debit"),
            ("3-2100", "Transfer dari Pusat", "equity", "capital", "credit"),
            ("3-2200", "Kirim ke Cabang", "equity", "capital", "debit"),
        ]:
            self.db.add(models.Account(code=code, name=name, type=kind, subtype=subtype,
                                       normal_balance=normal, is_active=True))
        self.db.flush()
        self.db.add_all([
            models.WarehouseStock(warehouse_id=self.wh_pusat.id, item_id=self.item.id, stock=0),
            models.WarehouseStock(warehouse_id=self.wh_cabang.id, item_id=self.item.id, stock=0),
        ])
        self.request = models.Purchase(
            number="REQ-C2-1", date=date(2026, 9, 2), branch_id=2, target_branch_id=1,
            status="processing", document_type="branch_request", is_branch_request=True,
            subtotal=1000, total=1000,
        )
        self.db.add(self.request)
        self.db.flush()
        self.request_line = models.PurchaseItem(
            purchase_id=self.request.id, item_id=self.item.id, qty=10,
            qty_ordered=10, qty_received=0, buy_price=100, total=1000,
        )
        self.db.add(self.request_line)
        self.db.flush()
        self.purchase = models.Purchase(
            number="PUR-C1-1", date=date(2026, 9, 2), branch_id=1, target_branch_id=2,
            from_po_id=self.request.id, supplier_id=self.supplier.id,
            status="unpaid", fulfillment_status="draft", subtotal=500, total=500,
            document_type="purchase", is_branch_request=False, created_by=self.admin_pusat.id,
        )
        self.db.add(self.purchase)
        self.db.flush()
        self.purchase_line = models.PurchaseItem(
            purchase_id=self.purchase.id, item_id=self.item.id, qty=5,
            qty_ordered=10, qty_received=5, buy_price=100, total=500,
            source_request_item_id=self.request_line.id,
        )
        self.db.add(self.purchase_line)
        self.db.flush()
        self.shipment = create_fulfillment_shipment(self.db, self.purchase)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def branch_stock(self):
        return self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.wh_cabang.id, item_id=self.item.id,
        ).one().stock

    def test_partial_shipment_uses_actual_quantity_and_request_stays_open(self):
        self.assertEqual(self.shipment.items[0].qty, 5)
        self.assertEqual(self.shipment.items[0].total_cost, 500)
        self.assertEqual(refresh_source_request_status(self.db, self.request), "shipping")

        result = receive_branch_request(
            self.purchase.id, db=self.db, current_user=self.admin_cabang,
        )
        self.assertEqual(result["status"], "partially_received")
        self.assertEqual(self.branch_stock(), 5)
        self.assertEqual(self.request_line.qty_received, 5)
        self.assertEqual(self.purchase.fulfillment_status, "received")
        self.assertEqual(self.shipment.status, "confirmed")

        with self.assertRaises(HTTPException) as duplicate:
            receive_branch_request(self.purchase.id, db=self.db, current_user=self.admin_cabang)
        self.assertEqual(duplicate.exception.status_code, 409)

    def test_branch_return_is_two_stage_and_preserves_purchase_provenance(self):
        receive_branch_request(self.purchase.id, db=self.db, current_user=self.admin_cabang)
        result = create_branch_return_to_center(
            schemas.BranchReturnCreate(
                date=date(2026, 9, 2),
                items=[schemas.BranchReturnItemCreate(purchase_item_id=self.purchase_line.id, qty=2)],
            ),
            db=self.db,
            current_user=self.admin_cabang,
        )
        self.assertEqual(self.branch_stock(), 3)
        transfer = self.db.query(models.WarehouseTransfer).get(result["id"])
        self.assertEqual(transfer.status, "shipping")
        self.assertEqual(transfer.items[0].purchase_item_id, self.purchase_line.id)
        self.assertEqual(self.item.stock, 0)

        received = receive_branch_return_at_center(
            transfer.id, db=self.db, current_user=self.admin_pusat,
        )
        self.assertEqual(received["status"], "confirmed")
        self.assertEqual(self.item.stock, 2)
        batch = self.db.query(models.StockBatch).filter_by(
            warehouse_id=self.wh_pusat.id, purchase_item_id=self.purchase_line.id,
        ).one()
        self.assertEqual(batch.qty_remaining, 2)
        self.assertEqual(batch.supplier_id, self.supplier.id)


if __name__ == "__main__":
    unittest.main()
