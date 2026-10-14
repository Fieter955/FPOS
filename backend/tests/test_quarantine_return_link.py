import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes import returns
from app.services.serials import assert_quarantine_serials


class QuarantineReturnLinkTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        branch = models.Branch(code="QRT", name="Cabang Karantina")
        self.db.add(branch)
        self.db.flush()
        warehouse = models.Warehouse(code="QRT-WH", name="Gudang", branch_id=branch.id,
                                     is_default=True, is_active=True)
        self.user = models.User(username="qrt-user", full_name="QRT User", hashed_password="x",
                                role="admin", is_active=True, branch_id=branch.id,
                                active_branch_id=branch.id)
        item = models.Item(code="QRT-1", name="Barang Rusak", buy_price=12, stock=0, is_active=True)
        self.db.add_all([warehouse, self.user, item])
        self.db.flush()
        self.quarantine = models.InventoryQuarantine(
            number="QRT-TEST", date=returns.get_local_date(), branch_id=branch.id,
            warehouse_id=warehouse.id, item_id=item.id, qty=3, qty_open=2,
            unit_cost=12, total_cost=36, source_type="warehouse_check",
            status="partial", created_by=self.user.id,
        )
        self.db.add(self.quarantine)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_inventory_quarantine_appears_as_broken_return_source(self):
        rows = returns.get_broken_history_items(db=self.db, current_user=self.user)
        row = next(value for value in rows if value["source_type"] == "inventory_quarantine")
        self.assertEqual(row["source_item_id"], self.quarantine.id)
        self.assertEqual(row["qty"], 3)
        self.assertEqual(row["qty_returned"], 0)
        self.assertEqual(row["qty_available"], 2)
        self.assertEqual(row["status"], "not_returned")
        self.assertEqual(row["quarantine_unit_cost"], 12)

    def test_inventory_quarantine_can_be_resolved_as_damaged_source(self):
        source = returns._damaged_source(
            self.db, "inventory_quarantine", self.quarantine.id,
            self.user.active_branch_id, for_update=True,
        )
        self.assertEqual(source.id, self.quarantine.id)

    def test_serial_from_another_quarantine_is_rejected(self):
        self.quarantine.item.tracks_serial = True
        other = models.InventoryQuarantine(
            number="QRT-OTHER", date=returns.get_local_date(),
            branch_id=self.quarantine.branch_id, warehouse_id=self.quarantine.warehouse_id,
            item_id=self.quarantine.item_id, qty=1, qty_open=1, unit_cost=12,
            total_cost=12, source_type="warehouse_check", status="open",
            created_by=self.user.id,
        )
        self.db.add(other)
        self.db.flush()
        serial = models.ItemSerial(
            item_id=self.quarantine.item_id, serial="SN-OTHER",
            normalized_serial="SN-OTHER", status="quarantine",
            warehouse_id=self.quarantine.warehouse_id,
            branch_id=self.quarantine.branch_id,
        )
        self.db.add(serial)
        self.db.flush()
        self.db.add(models.ItemSerialEvent(
            serial_id=serial.id, event_type="quarantined", from_status="in_stock",
            to_status="quarantine", from_warehouse_id=self.quarantine.warehouse_id,
            to_warehouse_id=self.quarantine.warehouse_id,
            reference_type="inventory_quarantine", reference_id=other.id,
            reference_number=other.number, created_by=self.user.id,
        ))
        self.db.commit()

        with self.assertRaisesRegex(HTTPException, "karantina lain"):
            assert_quarantine_serials(
                self.db, quarantine=self.quarantine, qty=1,
                serial_values=["SN-OTHER"],
            )


if __name__ == "__main__":
    unittest.main()
