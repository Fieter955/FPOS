import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.services import inventory_reconciliation as service
from app.services.inventory_fifo import add_batch, total_remaining


class InventoryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.branch = models.Branch(code="PST", name="Pusat")
        self.db.add(self.branch)
        self.db.flush()
        self.warehouse = models.Warehouse(
            code="PST-WH", name="Gudang Pusat", branch_id=self.branch.id,
            is_default=True, is_active=True,
        )
        self.user = models.User(
            username="admin-recon", full_name="Admin Recon", hashed_password="x",
            role="admin", is_active=True, branch_id=self.branch.id,
            active_branch_id=self.branch.id,
        )
        self.item = models.Item(code="REC-1", name="Barang Rekonsiliasi", buy_price=10, stock=9, is_active=True)
        self.db.add_all([self.warehouse, self.user, self.item])
        for code, name, kind in [
            ("1-1400", "Persediaan", "asset"),
            ("3-1999", "Modal Transisi", "equity"),
        ]:
            self.db.add(models.Account(code=code, name=name, type=kind, is_active=True))
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=self.warehouse.id, item_id=self.item.id, stock=5,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def apply_preview(self, preview, cost=None):
        lines = [schemas.InventoryReconciliationLineApply(
            item_id=line["item_id"], unit_cost=cost if line["qty_gap"] > 0 else None,
            allow_zero_cost=False,
        ) for line in preview["items"]]
        return service.apply(self.db, schemas.InventoryReconciliationApply(
            warehouse_id=self.warehouse.id,
            preview_token=preview["preview_token"],
            reason="Perbaikan data migrasi",
            lines=lines,
        ), self.user)

    def test_missing_batch_is_created_and_central_cache_and_gl_are_reconciled(self):
        preview = service.preview(self.db, self.warehouse.id, self.user)
        self.assertEqual(preview["items"][0]["qty_gap"], 5)

        rec = self.apply_preview(preview, cost=12)
        self.db.commit()

        self.assertEqual(total_remaining(self.db, self.item.id, self.warehouse.id), 5)
        self.db.refresh(self.item)
        self.assertEqual(self.item.stock, 5)
        self.assertEqual(rec.fifo_value_after, 60)
        self.assertEqual(rec.gl_after, 60)
        self.assertIsNotNone(rec.journal_id)
        self.assertEqual(rec.lines[0].action, "add_batch+sync_item_stock")

    def test_excess_fifo_is_trimmed_from_newest_batch(self):
        old = add_batch(
            self.db, item_id=self.item.id, warehouse_id=self.warehouse.id,
            qty=3, unit_cost=10, received_date=service.datetime(2026, 1, 1).date(),
        )
        new = add_batch(
            self.db, item_id=self.item.id, warehouse_id=self.warehouse.id,
            qty=4, unit_cost=20, received_date=service.datetime(2026, 2, 1).date(),
        )
        self.item.stock = 5
        self.db.commit()

        preview = service.preview(self.db, self.warehouse.id, self.user)
        self.assertEqual(preview["items"][0]["suggested_cost"], 20)
        self.assertEqual(preview["items"][0]["cost_source"], "HPP batch terbaru yang akan dikurangi")
        rec = self.apply_preview(preview)
        self.db.commit()
        self.db.refresh(old)
        self.db.refresh(new)

        self.assertEqual(old.qty_remaining, 3)
        self.assertEqual(new.qty_remaining, 2)
        self.assertEqual(rec.lines[0].action, "trim_newest_batch")
        self.assertEqual(rec.fifo_value_after, 70)

    def test_stale_preview_is_rejected(self):
        preview = service.preview(self.db, self.warehouse.id, self.user)
        self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.item.id,
        ).one().stock = 6
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self.apply_preview(preview, cost=10)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_other_branch_warehouse_is_rejected(self):
        branch = models.Branch(code="CBG", name="Cabang Lain")
        self.db.add(branch)
        self.db.flush()
        warehouse = models.Warehouse(
            code="CBG-WH", name="Gudang Lain", branch_id=branch.id,
            is_default=True, is_active=True,
        )
        self.db.add(warehouse)
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            service.preview(self.db, warehouse.id, self.user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_second_preview_is_balanced_after_apply(self):
        self.apply_preview(service.preview(self.db, self.warehouse.id, self.user), cost=10)
        self.db.commit()
        again = service.preview(self.db, self.warehouse.id, self.user)
        self.assertTrue(again["balanced"])
        self.assertEqual(again["items"], [])
        self.assertAlmostEqual(again["value_gap"], 0)


if __name__ == "__main__":
    unittest.main()
