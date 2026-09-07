import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.routes.branches import create_branch
from app.routes.items import create_item, get_items
from app.services.item_catalog_sync import backfill_catalog_stock_rows


class ItemCatalogSyncTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()

        self.pusat = models.Branch(
            code="HQ-01",
            name="Toko Pusat",
            status="Toko Utama",
            is_active=True,
        )
        self.db.add(self.pusat)
        self.db.flush()
        self.gudang_pusat = models.Warehouse(
            code="WH-HQ-01",
            name="Gudang Pusat",
            branch_id=self.pusat.id,
            is_default=True,
            is_active=True,
        )
        self.admin = models.User(
            username="admin-sync",
            full_name="Admin Sync",
            hashed_password="not-used",
            role="admin",
            is_active=True,
            branch_id=self.pusat.id,
            active_branch_id=self.pusat.id,
        )
        self.db.add_all([self.gudang_pusat, self.admin])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_new_branch_receives_existing_items_with_zero_stock(self):
        item = models.Item(code="OLD-1", name="Barang Pusat", stock=7, is_active=True)
        self.db.add(item)
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=self.gudang_pusat.id,
            item_id=item.id,
            stock=7,
        ))
        self.db.commit()

        cabang = create_branch(
            schemas.BranchCreate(
                code="AUTO",
                name="Cabang Baru",
                address="Alamat Cabang",
                status="Cabang",
            ),
            self.db,
            self.admin,
        )
        gudang_cabang = self.db.query(models.Warehouse).filter_by(branch_id=cabang.id).one()

        stok_pusat = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.gudang_pusat.id,
            item_id=item.id,
        ).one()
        stok_cabang = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=gudang_cabang.id,
            item_id=item.id,
        ).one()
        self.assertEqual(stok_pusat.stock, 7)
        self.assertEqual(stok_cabang.stock, 0)

    def test_item_created_from_branch_is_visible_in_every_branch(self):
        cabang = create_branch(
            schemas.BranchCreate(
                code="AUTO",
                name="Cabang Pembuat",
                address="Alamat Cabang",
                status="Cabang",
            ),
            self.db,
            self.admin,
        )
        gudang_cabang = self.db.query(models.Warehouse).filter_by(branch_id=cabang.id).one()
        self.admin.active_branch_id = cabang.id
        self.db.commit()

        item = create_item(
            schemas.ItemCreate(code="SYNC-NEW", name="Barang Dari Cabang"),
            self.db,
            self.admin,
        )

        rows = self.db.query(models.WarehouseStock).filter_by(item_id=item.id).all()
        self.assertEqual(
            {row.warehouse_id: row.stock for row in rows},
            {self.gudang_pusat.id: 0, gudang_cabang.id: 0},
        )
        visible = get_items(db=self.db, current_user=self.admin)
        self.assertIn(item.id, [row["id"] for row in visible])

    def test_backfill_is_idempotent_and_preserves_existing_stock(self):
        cabang = models.Branch(code="CBG-OLD", name="Cabang Lama", is_active=True)
        item = models.Item(code="BACKFILL", name="Barang Lama", is_active=True)
        self.db.add_all([cabang, item])
        self.db.flush()
        gudang_cabang = models.Warehouse(
            code="WH-CBG-OLD",
            name="Gudang Cabang Lama",
            branch_id=cabang.id,
            is_active=True,
        )
        self.db.add(gudang_cabang)
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=self.gudang_pusat.id,
            item_id=item.id,
            stock=12,
        ))
        self.db.commit()

        self.assertEqual(backfill_catalog_stock_rows(self.db), 1)
        self.db.commit()
        self.assertEqual(backfill_catalog_stock_rows(self.db), 0)
        self.db.commit()

        rows = self.db.query(models.WarehouseStock).filter_by(item_id=item.id).all()
        self.assertEqual(
            {row.warehouse_id: row.stock for row in rows},
            {self.gudang_pusat.id: 12, gudang_cabang.id: 0},
        )


if __name__ == "__main__":
    unittest.main()
