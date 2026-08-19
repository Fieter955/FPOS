import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.services.tax_context import _sale_line_ppn_rates, _sales_ppn_context


class PpnModeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.branch = models.Branch(
            id=1,
            code="PUSAT",
            name="Toko Pusat",
            is_pkp=False,
            tarif_ppn=11,
        )
        self.non_ppn = models.Item(code="NON", name="Barang Non-PPN", ppn_percent=0)
        self.follow_store = models.Item(code="STD", name="Ikut Tarif Toko", ppn_percent=None)
        self.custom_rate = models.Item(code="CUSTOM", name="Tarif Khusus", ppn_percent=12)
        self.db.add_all(
            [self.branch, self.non_ppn, self.follow_store, self.custom_rate]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _lines(self):
        # Nilai PPN palsu dari browser harus diabaikan; master barang yang berlaku.
        return [
            SimpleNamespace(item_id=self.non_ppn.id, ppn_percent=99),
            SimpleNamespace(item_id=self.follow_store.id, ppn_percent=0),
            SimpleNamespace(item_id=self.custom_rate.id, ppn_percent=0),
        ]

    def test_non_pkp_forces_all_sales_lines_to_zero(self):
        is_pkp, store_rate = _sales_ppn_context(self.db)
        self.assertFalse(is_pkp)
        self.assertEqual(store_rate, 0)
        self.assertEqual(
            _sale_line_ppn_rates(self.db, self._lines(), store_rate),
            [0, 0, 0],
        )

    def test_pkp_respects_non_ppn_and_item_rates(self):
        self.branch.is_pkp = True
        self.db.commit()

        is_pkp, store_rate = _sales_ppn_context(self.db)
        self.assertTrue(is_pkp)
        self.assertEqual(store_rate, 11)
        self.assertEqual(
            _sale_line_ppn_rates(self.db, self._lines(), store_rate),
            [0, 11, 12],
        )


if __name__ == "__main__":
    unittest.main()
