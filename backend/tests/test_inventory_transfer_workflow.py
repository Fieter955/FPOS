import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models
from app.routes.inventory import (
    QuarantineCreate,
    QuarantineBatchCreate,
    QuarantineBatchLine,
    QuarantineResolve,
    WarehouseMinimumUpdate,
    create_quarantine,
    create_quarantine_batch,
    get_item_snapshot,
    get_movements,
    resolve_quarantine,
    update_warehouse_minimum,
)
from app.routes.warehouse import (
    TransferCreate,
    TransferItemIn,
    TransferReceipt,
    TransferReceiptItem,
    TransferCancel,
    cancel_transfer,
    create_transfer_secure,
    receive_transfer,
)


class InventoryTransferWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.source_branch = models.Branch(code="SRC", name="Pusat")
        self.target_branch = models.Branch(code="DST", name="Cabang")
        self.db.add_all([self.source_branch, self.target_branch])
        self.db.flush()
        self.source = models.Warehouse(code="SRC-W", name="Etalase Pusat",
                                       branch_id=self.source_branch.id, is_default=True, is_active=True)
        self.target = models.Warehouse(code="DST-W", name="Etalase Cabang",
                                       branch_id=self.target_branch.id, is_default=True, is_active=True)
        self.unit = models.Unit(name="Pieces", abbreviation="PCS")
        self.db.add_all([self.source, self.target, self.unit])
        self.db.flush()
        self.item = models.Item(code="FLOW-1", name="Barang Workflow", unit_id=self.unit.id,
                                buy_price=10, sell_price=20, stock=5, is_active=True)
        self.user = models.User(username="workflow", full_name="Workflow", hashed_password="x",
                                role="admin", active_branch_id=self.source_branch.id, is_active=True)
        self.db.add_all([self.item, self.user])
        self.db.flush()
        self.db.add_all([
            models.WarehouseStock(warehouse_id=self.source.id, item_id=self.item.id, stock=5),
            models.WarehouseStock(warehouse_id=self.target.id, item_id=self.item.id, stock=0),
            models.StockBatch(item_id=self.item.id, warehouse_id=self.source.id,
                              unit_cost=10, qty_received=5, qty_remaining=5,
                              received_date=date(2026, 9, 1)),
        ])
        for code, name, account_type in [
            ("1-1400", "Persediaan", "asset"), ("1-1450", "Karantina", "asset"),
            ("3-2000", "Transfer", "equity"), ("5-1200", "Selisih", "expense"),
            ("2-1100", "Hutang", "liability"),
        ]:
            self.db.add(models.Account(code=code, name=name, type=account_type,
                                       normal_balance="debit" if account_type in {"asset", "expense"} else "credit"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_cross_branch_stock_moves_only_when_received(self):
        created = create_transfer_secure(TransferCreate(
            date=date.today(), from_warehouse_id=self.source.id,
            to_warehouse_id=self.target.id,
            items=[TransferItemIn(item_id=self.item.id, qty=3)],
        ), self.db, self.user)
        transfer = self.db.get(models.WarehouseTransfer, created["id"])
        self.assertEqual(transfer.status, "shipping")
        self.assertEqual(self.item.stock, 2)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 2)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.target.id, item_id=self.item.id).one().stock, 0)
        self.assertEqual(transfer.items[0].batch_allocations[0].destination_batch.qty_remaining, 0)

        self.user.active_branch_id = self.target_branch.id
        result = receive_transfer(transfer.id, TransferReceipt(items=[
            TransferReceiptItem(item_id=self.item.id, good_qty=2, damaged_qty=1, missing_qty=0),
        ]), self.db, self.user)
        self.assertEqual(result["status"], "received")
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.target.id, item_id=self.item.id).one().stock, 2)
        self.assertEqual(self.item.stock, 2)
        self.assertEqual(sum(
            batch.qty_remaining for batch in self.db.query(models.StockBatch).filter_by(
                warehouse_id=self.target.id, item_id=self.item.id
            )
        ), 2)
        quarantine = self.db.query(models.InventoryQuarantine).one()
        self.assertEqual(quarantine.qty_open, 1)

    def test_sale_filter_maps_legacy_out_type(self):
        self.db.add_all([
            models.StockMovement(branch_id=self.source_branch.id, date=date.today(),
                                 item_id=self.item.id, type="out", qty=1,
                                 notes="Penjualan Kasir"),
            models.StockMovement(branch_id=self.source_branch.id, date=date.today(),
                                 item_id=self.item.id, type="out", qty=1,
                                 notes="Pembatalan Pembelian"),
        ])
        self.db.commit()
        rows = get_movements(type="sale", db=self.db, current_user=self.user)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "sale")
        self.assertEqual(rows[0]["direction"], "out")

    def test_shipping_transfer_cancellation_restores_master_warehouse_and_fifo(self):
        created = create_transfer_secure(TransferCreate(
            date=date.today(), from_warehouse_id=self.source.id,
            to_warehouse_id=self.target.id,
            items=[TransferItemIn(item_id=self.item.id, qty=3)],
        ), self.db, self.user)
        cancel_transfer(
            created["id"], TransferCancel(reason="Batal kirim untuk pengujian"),
            self.db, self.user,
        )
        self.assertEqual(self.item.stock, 5)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 5)
        self.assertEqual(sum(
            batch.qty_remaining for batch in self.db.query(models.StockBatch).filter_by(
                warehouse_id=self.source.id, item_id=self.item.id
            )
        ), 5)

    def test_internal_transfer_does_not_change_central_master_total(self):
        rak_kedua = models.Warehouse(
            code="SRC-W-2", name="Rak Kedua Pusat",
            branch_id=self.source_branch.id, is_active=True,
        )
        self.db.add(rak_kedua)
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=rak_kedua.id, item_id=self.item.id, stock=0
        ))
        self.db.commit()

        create_transfer_secure(TransferCreate(
            date=date.today(), from_warehouse_id=self.source.id,
            to_warehouse_id=rak_kedua.id,
            items=[TransferItemIn(item_id=self.item.id, qty=3)],
        ), self.db, self.user)
        self.assertEqual(self.item.stock, 5)
        self.assertEqual(sum(
            row.stock for row in self.db.query(models.WarehouseStock)
            .join(models.Warehouse)
            .filter(
                models.Warehouse.branch_id == self.source_branch.id,
                models.WarehouseStock.item_id == self.item.id,
            )
        ), 5)
        self.assertEqual(sum(
            batch.qty_remaining for batch in self.db.query(models.StockBatch)
            .join(models.Warehouse)
            .filter(
                models.Warehouse.branch_id == self.source_branch.id,
                models.StockBatch.item_id == self.item.id,
            )
        ), 5)

    def test_branch_to_center_updates_master_only_when_received(self):
        self.item.stock = 0
        self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id
        ).one().stock = 0
        self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.target.id, item_id=self.item.id
        ).one().stock = 5
        batch = self.db.query(models.StockBatch).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id
        ).one()
        batch.warehouse_id = self.target.id
        self.user.active_branch_id = self.target_branch.id
        self.db.commit()

        created = create_transfer_secure(TransferCreate(
            date=date.today(), from_warehouse_id=self.target.id,
            to_warehouse_id=self.source.id,
            items=[TransferItemIn(item_id=self.item.id, qty=3)],
        ), self.db, self.user)
        self.assertEqual(self.item.stock, 0)

        self.user.active_branch_id = self.source_branch.id
        receive_transfer(created["id"], TransferReceipt(items=[
            TransferReceiptItem(item_id=self.item.id, good_qty=3, damaged_qty=0, missing_qty=0),
        ]), self.db, self.user)
        self.assertEqual(self.item.stock, 3)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 3)
        self.assertEqual(sum(
            layer.qty_remaining for layer in self.db.query(models.StockBatch).filter_by(
                warehouse_id=self.source.id, item_id=self.item.id
            )
        ), 3)

    def test_standalone_quarantine_uses_fifo_cost(self):
        result = create_quarantine(QuarantineCreate(
            date=date.today(), warehouse_id=self.source.id,
            item_id=self.item.id, qty=2, notes="Rusak saat inspeksi",
        ), self.db, self.user)
        self.assertEqual(result["unit_cost"], 10)
        self.assertEqual(result["qty_open"], 2)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 3)
        self.assertEqual(self.item.stock, 3)
        self.assertEqual(sum(
            batch.qty_remaining for batch in self.db.query(models.StockBatch).filter_by(
                warehouse_id=self.source.id, item_id=self.item.id
            )
        ), 3)
        movement = self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_out"
        ).one()
        self.assertEqual((movement.qty_before, movement.qty_after), (5, 3))
        self.assertEqual((movement.warehouse_qty_before, movement.warehouse_qty_after), (5, 3))

        quarantine = self.db.query(models.InventoryQuarantine).one()
        resolve_quarantine(
            quarantine.id,
            QuarantineResolve(action="restore", qty=1, notes="Layak dijual kembali"),
            self.db,
            self.user,
        )
        self.assertEqual(self.item.stock, 4)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 4)
        self.assertEqual(sum(
            batch.qty_remaining for batch in self.db.query(models.StockBatch).filter_by(
                warehouse_id=self.source.id, item_id=self.item.id
            )
        ), 4)

    def test_quarantine_batch_filters_supplier_and_saves_all_lines(self):
        supplier = models.Supplier(code="SUP-Q", name="Supplier Karantina", is_active=True)
        second = models.Item(code="FLOW-2", name="Barang Kedua", unit_id=self.unit.id,
                             buy_price=20, sell_price=30, stock=3, is_active=True)
        self.db.add_all([supplier, second])
        self.db.flush()
        self.db.add_all([
            models.ItemSupplier(item_id=self.item.id, supplier_id=supplier.id),
            models.ItemSupplier(item_id=second.id, supplier_id=supplier.id),
            models.WarehouseStock(warehouse_id=self.source.id, item_id=second.id, stock=3),
            models.StockBatch(item_id=second.id, warehouse_id=self.source.id,
                              unit_cost=20, qty_received=3, qty_remaining=3,
                              received_date=date(2026, 9, 1)),
        ])
        self.db.commit()

        filtered = get_item_snapshot(
            warehouse_id=self.source.id, supplier_id=supplier.id,
            db=self.db, current_user=self.user,
        )
        self.assertEqual({row["id"] for row in filtered}, {self.item.id, second.id})

        result = create_quarantine_batch(QuarantineBatchCreate(
            date=date.today(), warehouse_id=self.source.id, supplier_id=supplier.id,
            notes="Pemeriksaan batch", lines=[
                QuarantineBatchLine(item_id=self.item.id, qty=2),
                QuarantineBatchLine(item_id=second.id, qty=1),
            ],
        ), self.db, self.user)
        self.assertEqual(result["count"], 2)
        self.assertEqual(self.db.query(models.InventoryQuarantine).count(), 2)
        self.assertEqual(self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_out").count(), 2)

    def test_quarantine_writeoff_records_neutral_movement_without_changing_saleable_stock(self):
        created = create_quarantine(QuarantineCreate(
            date=date.today(), warehouse_id=self.source.id,
            item_id=self.item.id, qty=2, notes="Rusak permanen",
        ), self.db, self.user)
        saleable_before = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id,
        ).one().stock

        resolve_quarantine(
            created["id"],
            QuarantineResolve(action="writeoff", qty=1, notes="Tidak dapat diperbaiki"),
            self.db,
            self.user,
        )

        movement = self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_writeoff",
        ).one()
        quarantine = self.db.get(models.InventoryQuarantine, created["id"])
        self.assertEqual(movement.direction, "neutral")
        self.assertEqual((movement.qty_before, movement.qty_after), (saleable_before, saleable_before))
        self.assertIn("stok jual tetap", movement.notes)
        self.assertEqual(quarantine.qty_open, 1)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id,
        ).one().stock, saleable_before)

    def test_direct_supplier_resolution_is_rejected(self):
        created = create_quarantine(QuarantineCreate(
            date=date.today(), warehouse_id=self.source.id,
            item_id=self.item.id, qty=1,
        ), self.db, self.user)
        with self.assertRaisesRegex(HTTPException, "menu Retur"):
            resolve_quarantine(
                created["id"], QuarantineResolve(action="supplier_return", qty=1),
                self.db, self.user,
            )

    def test_quarantine_resolution_rolls_back_movement_and_balance_when_journal_fails(self):
        created = create_quarantine(QuarantineCreate(
            date=date.today(), warehouse_id=self.source.id,
            item_id=self.item.id, qty=1,
        ), self.db, self.user)
        with patch(
            "app.services.journal_service.create_quarantine_resolution_journal",
            side_effect=RuntimeError("journal gagal"),
        ):
            with self.assertRaisesRegex(RuntimeError, "journal gagal"):
                resolve_quarantine(
                    created["id"], QuarantineResolve(action="writeoff", qty=1),
                    self.db, self.user,
                )
        quarantine = self.db.get(models.InventoryQuarantine, created["id"])
        self.assertEqual(quarantine.qty_open, 1)
        self.assertEqual(self.db.query(models.StockMovement).filter_by(
            event_category="quarantine_writeoff",
        ).count(), 0)

    def test_item_snapshot_exposes_and_updates_warehouse_minimum(self):
        self.item.min_stock = 3
        stock_row = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id,
        ).one()
        stock_row.min_stock = 5
        self.db.commit()

        snapshot = get_item_snapshot(
            warehouse_id=self.source.id, item_id=self.item.id,
            db=self.db, current_user=self.user,
        )[0]
        self.assertEqual(snapshot["master_min_stock"], 3)
        self.assertEqual(snapshot["warehouse_min_stock"], 5)
        self.assertEqual(snapshot["effective_min_stock"], 5)
        self.assertFalse(snapshot["uses_master_default"])

        result = update_warehouse_minimum(
            WarehouseMinimumUpdate(
                warehouse_id=self.source.id, item_id=self.item.id, min_stock=None,
            ),
            self.db,
            self.user,
        )
        self.assertTrue(result["uses_master_default"])
        refreshed = get_item_snapshot(
            warehouse_id=self.source.id, item_id=self.item.id,
            db=self.db, current_user=self.user,
        )[0]
        self.assertIsNone(refreshed["warehouse_min_stock"])
        self.assertEqual(refreshed["effective_min_stock"], 3)
        self.assertTrue(refreshed["uses_master_default"])

    def test_quarantine_batch_rolls_back_when_one_line_fails(self):
        second = models.Item(code="FLOW-3", name="Stok Tipis", unit_id=self.unit.id,
                             buy_price=15, sell_price=25, stock=1, is_active=True)
        self.db.add(second)
        self.db.flush()
        self.db.add_all([
            models.WarehouseStock(warehouse_id=self.source.id, item_id=second.id, stock=1),
            models.StockBatch(item_id=second.id, warehouse_id=self.source.id,
                              unit_cost=15, qty_received=1, qty_remaining=1,
                              received_date=date(2026, 9, 1)),
        ])
        self.db.commit()

        with self.assertRaises(HTTPException):
            create_quarantine_batch(QuarantineBatchCreate(
                date=date.today(), warehouse_id=self.source.id, lines=[
                    QuarantineBatchLine(item_id=self.item.id, qty=2),
                    QuarantineBatchLine(item_id=second.id, qty=2),
                ],
            ), self.db, self.user)

        self.assertEqual(self.db.query(models.InventoryQuarantine).count(), 0)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 5)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=second.id).one().stock, 1)

    def test_quarantine_batch_rejects_duplicates_and_supplier_mismatch(self):
        supplier = models.Supplier(code="SUP-X", name="Supplier Lain", is_active=True)
        self.db.add(supplier)
        self.db.commit()

        with self.assertRaises(HTTPException):
            create_quarantine_batch(QuarantineBatchCreate(
                date=date.today(), warehouse_id=self.source.id, lines=[
                    QuarantineBatchLine(item_id=self.item.id, qty=1),
                    QuarantineBatchLine(item_id=self.item.id, qty=1),
                ],
            ), self.db, self.user)
        with self.assertRaises(HTTPException):
            create_quarantine_batch(QuarantineBatchCreate(
                date=date.today(), warehouse_id=self.source.id, supplier_id=supplier.id,
                lines=[QuarantineBatchLine(item_id=self.item.id, qty=1)],
            ), self.db, self.user)

        self.assertEqual(self.db.query(models.InventoryQuarantine).count(), 0)
        self.assertEqual(self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.source.id, item_id=self.item.id).one().stock, 5)


if __name__ == "__main__":
    unittest.main()
