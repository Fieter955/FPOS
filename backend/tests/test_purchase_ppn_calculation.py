import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.routes.purchases import get_items_for_purchase
from app.services.purchase_flow import calculate_purchase_totals
from app.services.tax_context import normalize_purchase_tax_type, purchase_line_ppn_rates


class PurchasePpnCalculationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(models.Branch(id=1, code="PUSAT", name="Toko Pusat", tarif_ppn=11))
        self.item = models.Item(code="A", name="Barang A", ppn_percent=None)
        self.non_ppn = models.Item(code="N", name="Barang Non PPN", ppn_percent=0)
        self.supplier = models.Supplier(code="SUP", name="Supplier", PpnSupplier=11)
        self.db.add_all([self.item, self.non_ppn, self.supplier])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def line(self, item_id, price, ppn_percent=None, qty=1):
        return schemas.PurchaseItemCreate(
            item_id=item_id,
            qty=qty,
            buy_price=price,
            ppn_percent=ppn_percent,
        )

    def purchase(self, tax_type, lines, *, tax=0, discount=0):
        return schemas.PurchaseCreate(
            date=date(2026, 8, 16),
            supplier_id=self.supplier.id,
            discount=discount,
            tax=tax,
            tax_percent=tax,
            tax_type=tax_type,
            is_tax_included=tax_type == "include",
            items=lines,
        )

    def assert_totals(self, actual, subtotal, discount, tax, total):
        self.assertAlmostEqual(actual["subtotal"], subtotal, places=6)
        self.assertAlmostEqual(actual["discount"], discount, places=6)
        self.assertAlmostEqual(actual["tax"], tax, places=6)
        self.assertAlmostEqual(actual["total"], total, places=6)

    def test_include_pkp_peels_tax_after_header_discount(self):
        data = self.purchase(
            "include", [self.line(self.item.id, 111_000, 11)], discount=10
        )
        totals = calculate_purchase_totals(data, peel_included=True)
        self.assert_totals(totals, 100_000, 10_000, 9_900, 99_900)

    def test_include_pkp_supports_different_line_rates(self):
        data = self.purchase(
            "include",
            [
                self.line(self.item.id, 111_000, 11),
                self.line(self.non_ppn.id, 120_000, 20),
            ],
        )
        totals = calculate_purchase_totals(data, peel_included=True)
        self.assert_totals(totals, 200_000, 0, 31_000, 231_000)

    def test_include_non_pkp_keeps_price_as_inventory_cost(self):
        data = self.purchase("include", [self.line(self.item.id, 111_000, 11)], tax=11)
        totals = calculate_purchase_totals(data, peel_included=False)
        self.assert_totals(totals, 111_000, 0, 0, 111_000)

    def test_exclude_adds_header_tax(self):
        data = self.purchase("exclude", [self.line(self.item.id, 100_000, 11)], tax=11)
        totals = calculate_purchase_totals(data)
        self.assert_totals(totals, 100_000, 0, 11_000, 111_000)

    def test_none_forces_tax_to_zero_even_if_client_sends_rate(self):
        data = self.purchase("none", [self.line(self.item.id, 100_000, 11)], tax=11)
        totals = calculate_purchase_totals(data)
        self.assert_totals(totals, 100_000, 0, 0, 100_000)

    def test_missing_line_rate_uses_supplier_then_item_master_then_store(self):
        supplier_item = models.ItemSupplier(
            item_id=self.item.id,
            supplier_id=self.supplier.id,
            ppn_type="included",
            ppn_percent=12,
        )
        self.db.add(supplier_item)
        self.db.commit()
        data = self.purchase("include", [self.line(self.item.id, 112_000)])
        self.assertEqual(purchase_line_ppn_rates(self.db, data), [12.0])

    def test_purchase_item_list_uses_supplier_default_when_item_supplier_rate_is_empty(self):
        self.supplier.PpnSupplier = 12
        self.db.add(
            models.ItemSupplier(
                item_id=self.item.id,
                supplier_id=self.supplier.id,
                ppn_type="included",
                ppn_percent=0,
            )
        )
        self.db.commit()

        rows = get_items_for_purchase(
            supplier_id=self.supplier.id,
            db=self.db,
            current_user=None,
        )

        selected = next(row for row in rows if row["id"] == self.item.id)
        self.assertEqual(selected["ppn_percent"], 12.0)
        self.assertEqual(selected["ppn_type"], "included")

    def test_purchase_item_list_keeps_explicit_no_ppn_override(self):
        self.supplier.PpnSupplier = 12
        self.db.add(
            models.ItemSupplier(
                item_id=self.item.id,
                supplier_id=self.supplier.id,
                ppn_type="none",
                ppn_percent=0,
            )
        )
        self.db.commit()

        rows = get_items_for_purchase(
            supplier_id=self.supplier.id,
            db=self.db,
            current_user=None,
        )

        selected = next(row for row in rows if row["id"] == self.item.id)
        self.assertEqual(selected["ppn_percent"], 0)
        self.assertEqual(selected["ppn_type"], "none")

    def test_legacy_mode_fallback_is_stable(self):
        self.assertEqual(normalize_purchase_tax_type(None, is_tax_included=True), "include")
        self.assertEqual(normalize_purchase_tax_type(None, is_tax_included=False), "exclude")
        self.assertEqual(normalize_purchase_tax_type("none", is_tax_included=True), "none")


if __name__ == "__main__":
    unittest.main()
