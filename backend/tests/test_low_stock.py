import unittest
import sys
import types
from datetime import timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(
    DATABASE_URL="sqlite:///:memory:",
    SECRET_KEY="test-secret",
    ALGORITHM="HS256",
)
sys.modules.setdefault("app.config", config_stub)

pytz_stub = types.ModuleType("pytz")
pytz_stub.timezone = lambda _name: timezone.utc
sys.modules.setdefault("pytz", pytz_stub)

from app.database import Base
from app import models
from app.services.low_stock import get_low_stock_items


class LowStockServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()

        self.branch = models.Branch(code="LOW", name="Cabang Stok Menipis")
        self.db.add(self.branch)
        self.db.flush()
        self.warehouse = models.Warehouse(
            code="LOW-WH",
            name="Gudang Stok Menipis",
            branch_id=self.branch.id,
            is_default=True,
        )
        self.db.add(self.warehouse)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def add_item(self, code, name, stock, min_stock, **kwargs):
        item = models.Item(
            code=code,
            name=name,
            min_stock=min_stock,
            stock=999,
            **kwargs,
        )
        self.db.add(item)
        self.db.flush()
        self.db.add(
            models.WarehouseStock(
                warehouse_id=self.warehouse.id,
                item_id=item.id,
                stock=stock,
            )
        )
        return item

    def test_returns_branch_stock_with_status_and_urgency_order(self):
        depleted = self.add_item("EMPTY", "Barang Habis", 0, 5)
        low = self.add_item("LOW", "Barang Menipis", 2, 10)
        self.add_item("SAFE", "Barang Aman", 11, 10)
        self.add_item(
            "INACTIVE",
            "Barang Nonaktif",
            0,
            10,
            is_active=False,
        )
        self.db.commit()

        result = get_low_stock_items(self.db, self.branch.id)

        self.assertEqual([row["id"] for row in result], [depleted.id, low.id])
        self.assertEqual(result[0]["status"], "out_of_stock")
        self.assertEqual(result[1]["status"], "low")
        self.assertEqual(result[1]["stock"], 2)
        self.assertEqual(result[1]["min_stock"], 10)

    def test_virtual_variant_uses_converted_parent_stock(self):
        parent = self.add_item("BOX", "Barang Dus", 1, 0)
        child = models.Item(
            code="PCS",
            name="Barang Eceran",
            min_stock=3,
            stock=999,
            parent_item_id=parent.id,
            conversion_factor_to_parent=0.5,
            is_virtual_variant=True,
        )
        self.db.add(child)
        self.db.commit()

        result = get_low_stock_items(self.db, self.branch.id)

        child_result = next(row for row in result if row["id"] == child.id)
        self.assertEqual(child_result["stock"], 2)
        self.assertEqual(child_result["status"], "low")

    def test_uses_legacy_item_stock_when_no_warehouse_exists(self):
        other_branch = models.Branch(code="NOWH", name="Cabang Tanpa Gudang")
        item = models.Item(
            code="LEGACY",
            name="Barang Lama",
            stock=1,
            min_stock=2,
        )
        self.db.add_all([other_branch, item])
        self.db.commit()

        result = get_low_stock_items(self.db, other_branch.id)

        legacy = next(row for row in result if row["id"] == item.id)
        self.assertEqual(legacy["stock"], 1)


if __name__ == "__main__":
    unittest.main()
