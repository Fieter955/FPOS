import sys
import types
import unittest
from datetime import date, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(DATABASE_URL="sqlite:///:memory:", SECRET_KEY="test", ALGORITHM="HS256")
sys.modules.setdefault("app.config", config_stub)
pytz_stub = types.ModuleType("pytz")
pytz_stub.timezone = lambda _name: timezone.utc
sys.modules.setdefault("pytz", pytz_stub)

from app.database import Base
from app import models, schemas
from app.routes.unit_conversion import RebasePayload, confirm_rebase, preview_rebase
from app.services.purchase_flow import add_purchase_items


class UnitRebaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        # Samakan dengan SessionLocal produksi; ini menangkap duplikasi objek
        # pending yang tidak terlihat oleh query saat autoflush dimatikan.
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.branch = models.Branch(code="B1", name="Cabang")
        self.db.add(self.branch)
        self.db.flush()
        self.user = models.User(username="admin", hashed_password="x", active_branch_id=self.branch.id)
        self.dus = models.Unit(name="Dus", abbreviation="dus")
        self.pcs = models.Unit(name="Pcs", abbreviation="pcs")
        self.db.add_all([self.user, self.dus, self.pcs])
        self.db.flush()
        self.warehouse = models.Warehouse(code="WH", name="Gudang", branch_id=self.branch.id, is_default=True)
        self.source = models.Item(code="BRG", name="Barang Dus", unit_id=self.dus.id, buy_price=250000,
                                  sell_price=300000, stock=2, is_active=True, is_virtual_variant=False)
        self.db.add_all([self.warehouse, self.source])
        self.db.flush()
        self.db.add(models.WarehouseStock(warehouse_id=self.warehouse.id, item_id=self.source.id, stock=2))
        self.db.add(models.StockBatch(item_id=self.source.id, warehouse_id=self.warehouse.id,
                                      qty_received=2, qty_remaining=2, unit_cost=250000,
                                      received_date=date(2026, 1, 1)))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_rebase_preserves_value_and_old_sku_becomes_box_variant(self):
        supplier = models.Supplier(code="SUP-MASTER", name="Supplier Master")
        group = models.CustomerGroup(name="Member")
        self.db.add_all([supplier, group])
        self.db.flush()
        self.source.ppn_percent = 11
        self.source.is_discountable = True
        self.source.image_path = "/uploads/items/source.webp"
        self.db.add(models.ItemSupplier(
            item_id=self.source.id, supplier_id=supplier.id,
            buy_price=250000, ppn_type="included", ppn_percent=11,
        ))
        self.db.add(models.ItemGroupDiscount(
            item_id=self.source.id, group_id=group.id, disc1=5,
        ))
        self.db.add(models.ItemPrice(
            item_id=self.source.id, name="Member", price=290000, min_qty=1,
        ))
        self.db.commit()
        payload = RebasePayload(source_item_id=self.source.id, base_unit_id=self.pcs.id,
                                units_per_source=25, base_name="Barang PCS", base_code="BRG-PCS",
                                base_sell_price=12500,
                                prices=[schemas.ItemPriceCreate(name="Member", price=11600, min_qty=1)])
        preview = preview_rebase(payload, self.db, self.user)
        self.assertEqual(preview["total_after"], 50)
        self.assertEqual(preview["inventory_value_before"], 500000)
        payload.snapshot_token = preview["snapshot_token"]
        result = confirm_rebase(payload, self.db, self.user)

        base = self.db.get(models.Item, result["base_item_id"])
        source = self.db.get(models.Item, self.source.id)
        batch = self.db.query(models.StockBatch).one()
        self.assertEqual(base.stock, 50)
        base_warehouse_stocks = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=base.id,
        ).all()
        self.assertEqual(len(base_warehouse_stocks), 1)
        self.assertEqual(base_warehouse_stocks[0].stock, 50)
        self.assertEqual(source.code, "BRG")
        self.assertTrue(source.is_virtual_variant)
        self.assertEqual(source.parent_item_id, base.id)
        self.assertEqual(source.conversion_factor_to_parent, 25)
        self.assertEqual(batch.item_id, base.id)
        self.assertEqual(batch.qty_remaining, 50)
        self.assertEqual(batch.unit_cost, 10000)
        self.assertEqual(batch.qty_remaining * batch.unit_cost, 500000)
        self.assertEqual(base.sell_price, 12500)
        self.assertEqual(base.ppn_percent, 11)
        self.assertTrue(base.is_discountable)
        self.assertEqual(base.image_path, "/uploads/items/source.webp")
        self.assertEqual(base.supplier_details[0].buy_price, 10000)
        self.assertEqual(base.group_discounts[0].disc1, 5)
        self.assertEqual(base.prices[0].price, 11600)

    def test_purchase_in_box_records_base_quantity_and_entered_unit(self):
        base = models.Item(code="BASE", name="Barang PCS", unit_id=self.pcs.id, buy_price=10000, is_active=True)
        self.db.add(base)
        self.db.flush()
        variant = models.Item(code="BOX", name="Barang Dus", unit_id=self.dus.id, parent_item_id=base.id,
                              conversion_factor_to_parent=25, is_virtual_variant=True, is_active=True)
        supplier = models.Supplier(code="SUP", name="Supplier")
        self.db.add_all([variant, supplier])
        self.db.flush()
        purchase = models.Purchase(number="P-1", date=date(2026, 1, 1), branch_id=self.branch.id,
                                   supplier_id=supplier.id, total=500000, status="unpaid")
        self.db.add(purchase)
        self.db.flush()
        data = schemas.PurchaseCreate(date=date(2026, 1, 1), supplier_id=supplier.id,
            items=[schemas.PurchaseItemCreate(item_id=variant.id, qty=2, qty_received=2, buy_price=250000)])
        add_purchase_items(self.db, purchase, data, received=True)
        self.db.flush()

        line = purchase.items[0]
        self.assertEqual(line.item_id, base.id)
        self.assertEqual(line.entered_item_id, variant.id)
        self.assertEqual(line.entered_qty, 2)
        self.assertEqual(line.qty, 50)
        self.assertEqual(line.buy_price, 10000)
        self.assertEqual(line.total, 500000)

        purchase_pcs = models.Purchase(number="P-2", date=date(2026, 1, 2), branch_id=self.branch.id,
                                       supplier_id=supplier.id, total=30000, status="unpaid")
        self.db.add(purchase_pcs)
        self.db.flush()
        data_pcs = schemas.PurchaseCreate(date=date(2026, 1, 2), supplier_id=supplier.id,
            items=[schemas.PurchaseItemCreate(item_id=base.id, qty=3, qty_received=3, buy_price=10000)])
        add_purchase_items(self.db, purchase_pcs, data_pcs, received=True)
        self.db.flush()
        pcs_line = purchase_pcs.items[0]
        self.assertEqual(pcs_line.item_id, base.id)
        self.assertEqual(pcs_line.entered_item_id, base.id)
        self.assertEqual(pcs_line.qty, 3)
        self.assertEqual(pcs_line.buy_price, 10000)

    def test_rebase_scales_existing_group_and_quantity_prices_by_default(self):
        self.db.add_all([
            models.ItemPrice(item_id=self.source.id, name="Member", price=275000, min_qty=1),
            models.ItemPrice(item_id=self.source.id, name="Grosir", price=250000, min_qty=3),
        ])
        self.db.commit()
        payload = RebasePayload(
            source_item_id=self.source.id,
            base_unit_id=self.pcs.id,
            units_per_source=25,
            base_name="Barang PCS",
            base_code="BRG-PCS",
        )
        preview = preview_rebase(payload, self.db, self.user)
        payload.snapshot_token = preview["snapshot_token"]
        result = confirm_rebase(payload, self.db, self.user)
        base = self.db.get(models.Item, result["base_item_id"])
        prices = {price.name: price for price in base.prices}
        self.assertEqual(prices["Member"].price, 11000)
        self.assertEqual(prices["Member"].min_qty, 1)
        self.assertEqual(prices["Grosir"].price, 10000)
        self.assertEqual(prices["Grosir"].min_qty, 75)


if __name__ == "__main__":
    unittest.main()
