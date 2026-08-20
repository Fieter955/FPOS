import gzip
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes.accounting import SetupBalanceIn, setup_initial_balance
from app.routes.items import _import_items_sync
from app.services import ipos_seed
from generate_ipos_seed import build_payload


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
ASSET_PATH = BACKEND_DIR / "app" / "ipos_seed_v1.json.gz"


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False)()


def add_infrastructure(db):
    branch = models.Branch(code="HQ-01", name="Toko Pusat", is_active=True)
    db.add(branch)
    db.flush()
    warehouse = models.Warehouse(
        code="WH-HQ-01",
        name="Gudang Utama",
        branch_id=branch.id,
        is_active=True,
        is_default=True,
    )
    admin = models.User(
        username="admin",
        full_name="Administrator",
        hashed_password="not-used-in-test",
        role="admin",
        is_active=True,
        branch_id=branch.id,
        active_branch_id=branch.id,
    )
    db.add_all([
        warehouse,
        admin,
        models.Category(name="Umum"),
        models.Brand(name="Tanpa Merek"),
        models.Unit(name="Pcs", abbreviation="pcs"),
    ])
    db.commit()
    return branch, warehouse, admin


class IposSeedAssetTests(unittest.TestCase):
    def test_four_workbooks_generate_the_bundled_payload(self):
        generated = build_payload(REPO_DIR / "dataipos")
        with gzip.open(ASSET_PATH, "rt", encoding="utf-8") as stream:
            bundled = json.load(stream)
        self.assertEqual(generated, bundled)
        self.assertEqual(generated["source_item_count"], 1848)
        self.assertEqual(
            generated["price_mode_counts"],
            {"S": 229, "L": 1609, "J": 10, "none": 2},
        )

    def test_runtime_packaging_and_onboarding_contract(self):
        spec = (BACKEND_DIR / "FPOS.spec").read_text(encoding="utf-8")
        onboarding = (REPO_DIR / "frontend" / "onboarding.html").read_text(encoding="utf-8")
        item_form = (REPO_DIR / "frontend" / "js" / "item" / "itemForm.js").read_text(encoding="utf-8")
        self.assertIn("app/ipos_seed_v1.json.gz", spec)
        self.assertIn('id="iposSeedCard"', onboarding)
        self.assertIn('api("GET", "/onboarding/status")', onboarding)
        self.assertIn("Harga grup dan harga berdasarkan jumlah boleh hidup bersamaan", item_form)


class IposAutomaticSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_asset = os.environ.get("IPOS_SEED_PATH")
        os.environ["IPOS_SEED_PATH"] = str(ASSET_PATH)
        cls.engine, cls.db = make_session()
        cls.branch, cls.warehouse, cls.admin = add_infrastructure(cls.db)
        cls.first_status = ipos_seed.run_automatic_seed(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.engine.dispose()
        if cls.old_asset is None:
            os.environ.pop("IPOS_SEED_PATH", None)
        else:
            os.environ["IPOS_SEED_PATH"] = cls.old_asset

    def item(self, code):
        return self.db.query(models.Item).filter(models.Item.code == code).one()

    def test_seed_counts_and_restart_are_idempotent(self):
        self.assertEqual(self.first_status["status"], "completed")
        self.assertEqual(self.first_status["counts"]["items"], 2094)
        self.assertEqual(self.first_status["counts"]["virtual_variants"], 253)
        self.assertEqual(self.first_status["counts"]["opening_stock_lines"], 1666)
        self.assertEqual(self.db.query(models.BillOfMaterial).count(), 8)
        self.assertEqual(self.db.query(models.InventoryDocument).count(), 1)

        second = ipos_seed.run_automatic_seed(self.db)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(self.db.query(models.Item).count(), 2094)
        self.assertEqual(self.db.query(models.InventoryDocument).count(), 1)

    def test_item_corrections_and_duplicate_names(self):
        self.assertIsNone(self.db.query(models.Item).filter_by(code="TC1777").first())
        self.assertIsNone(self.db.query(models.Item).filter_by(code="TC1778").first())
        self.assertFalse(self.item("TC2134").is_active)
        self.assertEqual(self.item("TC2134").stock, 1)
        self.assertFalse(self.item("TH0339").is_active)
        self.assertEqual(self.item("TC17").name, self.item("TC1737").name)
        self.assertFalse(self.item("TC17").is_virtual_variant)
        self.assertFalse(self.item("TC1737").is_virtual_variant)
        self.assertEqual(self.item("AV0116").stock, 0)
        self.assertFalse(self.item("TC1769").is_virtual_variant)

    def test_virtual_units_use_declared_conversion(self):
        tc1690 = self.item("TC1690")
        self.assertTrue(tc1690.is_virtual_variant)
        self.assertEqual(tc1690.parent_item_id, self.item("TC0804").id)
        self.assertAlmostEqual(tc1690.conversion_factor_to_parent, 0.4)
        self.assertAlmostEqual(self.item("TC0804").stock, 116.0)

        for child_code, parent_code in (
            ("TC1765", "TC1756"),
            ("TC1766", "TC1757"),
            ("TC1767", "TC1758"),
        ):
            child = self.item(child_code)
            self.assertEqual(child.parent_item_id, self.item(parent_code).id)
            self.assertAlmostEqual(child.conversion_factor_to_parent, 10.0)

        conflict_child = self.item("TC0573-U2")
        self.assertAlmostEqual(conflict_child.conversion_factor_to_parent, 0.2)
        self.assertAlmostEqual(
            conflict_child.buy_price,
            self.item("TC0573").buy_price * 0.2,
        )

    def test_prices_tax_and_fifo_normalization(self):
        mixed = self.item("TC0491")
        self.assertAlmostEqual(mixed.sell_price, 4000)
        tiers = [(p.min_qty, p.price) for p in mixed.prices if p.name == "Grosir"]
        self.assertEqual(tiers, [(2.0, 3333.33)])

        level = self.item("TC0892")
        prices = {p.name: p.price for p in level.prices}
        self.assertEqual(
            prices,
            {"Level 2": 1800, "Level 3": 1500, "Level 4": 1000},
        )
        self.assertEqual(self.item("TC1242").ppn_percent, 12)
        self.assertEqual(self.item("NDP0002").ppn_percent, 0)
        self.assertEqual(self.item("NDP0090").ppn_percent, 0)

    def test_bom_recipe_and_opening_journal(self):
        expected = {
            "1900": {"TC1898": 50.0},
            "TC1638": {"TC1089": 100.0},
            "TC1653": {"TC1652": 4.0},
            "TC1689": {"TC0804": 0.2},
            "TC1691": {"TC1637": 100.0},
            "TC1692": {"TC1637": 50.0},
            "TC1693": {"TC1087": 50.0},
            "TC1764": {"TC1763": 0.13, "TC1758": 1.0},
        }
        actual = {}
        for bom in self.db.query(models.BillOfMaterial).all():
            actual[bom.product.code] = {
                line.material.code: line.qty_needed
                for line in self.db.query(models.BOMLine).filter_by(bom_id=bom.id).all()
            }
        self.assertEqual(actual, expected)

        document = self.db.query(models.InventoryDocument).one()
        self.assertEqual(document.type, "opening_stock")
        self.assertEqual(document.status, "posted")
        self.assertEqual(len(document.lines), 1666)
        debit = sum(line.amount for line in document.journal.lines if line.debit_account_id)
        credit = sum(line.amount for line in document.journal.lines if line.credit_account_id)
        self.assertAlmostEqual(debit, credit, places=2)
        self.assertEqual(self.db.query(models.StockBatch).count(), 1666)

    def test_onboarding_does_not_post_inventory_twice(self):
        journals_before = self.db.query(models.Journal).count()
        setup_initial_balance(
            SetupBalanceIn(inventory=999999),
            db=self.db,
            current_user=self.admin,
        )
        self.assertEqual(self.db.query(models.Journal).count(), journals_before)


class IposSeedGuardTests(unittest.TestCase):
    def setUp(self):
        self.old_asset = os.environ.get("IPOS_SEED_PATH")
        os.environ["IPOS_SEED_PATH"] = str(ASSET_PATH)
        self.engine, self.db = make_session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.old_asset is None:
            os.environ.pop("IPOS_SEED_PATH", None)
        else:
            os.environ["IPOS_SEED_PATH"] = self.old_asset

    def test_existing_database_is_marked_skipped(self):
        self.db.add(models.Item(code="OLD-1", name="Barang Lama"))
        self.db.commit()
        status = ipos_seed.run_automatic_seed(self.db)
        self.assertEqual(status["status"], "skipped_existing_data")
        self.assertEqual(self.db.query(models.Item).count(), 1)

    def test_failure_rolls_back_partial_seed_and_can_retry(self):
        def fail_after_write(db, _payload):
            db.add(models.Item(code="PARTIAL", name="Tidak Boleh Tersisa"))
            db.flush()
            raise RuntimeError("simulated seed failure")

        with patch("app.services.ipos_seed._apply_seed", side_effect=fail_after_write):
            status = ipos_seed.run_automatic_seed(self.db)
        self.assertEqual(status["status"], "failed")
        self.assertIn("simulated seed failure", status["error"])
        self.assertEqual(self.db.query(models.Item).count(), 0)


class DuplicateItemNameImportTests(unittest.TestCase):
    def test_generic_import_uses_code_instead_of_name_as_identity(self):
        engine, db = make_session()
        try:
            _branch, _warehouse, admin = add_infrastructure(db)
            csv_data = (
                "KODEITEM,KODEBARCODE,NAMAITEM,JENIS,MEREK,SATUAN,HARGAPOKOK,HARGAJUAL,STOK,STOKMIN,KETERANGAN,SUPPLIER\n"
                "DUP-1,,Nama Sama,Umum,,Pcs,10,20,0,0,,\n"
                "DUP-2,,Nama Sama,Umum,,Pcs,10,25,0,0,,\n"
            ).encode("utf-8")
            result = _import_items_sync(csv_data, "duplicate.csv", db, admin)
            self.assertEqual(result["imported"], 2)
            self.assertEqual(
                {row.code for row in db.query(models.Item).filter(models.Item.name == "Nama Sama")},
                {"DUP-1", "DUP-2"},
            )
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
