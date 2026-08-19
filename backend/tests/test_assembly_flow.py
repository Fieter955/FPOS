import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes import assembly
from app.services.inventory_fifo import add_batch, total_remaining


class AssemblyFlowTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        self.branch = models.Branch(code="B1", name="Cabang Satu")
        self.db.add(self.branch)
        self.db.flush()
        self.user = models.User(
            username="assembler",
            full_name="Assembler",
            hashed_password="x",
            role="admin",
            branch_id=self.branch.id,
            active_branch_id=self.branch.id,
        )
        self.customer = models.Customer(code="C1", name="Pelanggan")
        self.warehouse = models.Warehouse(
            code="W1",
            name="Gudang Utama",
            branch_id=self.branch.id,
            is_default=True,
        )
        self.material = models.Item(code="MAT", name="Bahan", stock=15, buy_price=2)
        self.product = models.Item(code="PROD", name="Produk Jadi", stock=0, buy_price=0)
        self.db.add_all([self.user, self.customer, self.warehouse, self.material, self.product])
        self.db.flush()
        self.db.add(models.WarehouseStock(
            warehouse_id=self.warehouse.id,
            item_id=self.material.id,
            stock=15,
        ))
        add_batch(
            self.db,
            item_id=self.material.id,
            warehouse_id=self.warehouse.id,
            qty=15,
            unit_cost=2,
            received_date=date(2026, 1, 1),
        )
        self.bom = models.BillOfMaterial(
            product_id=self.product.id,
            qty_produced=2,
            operational_cost=4,
            is_active=True,
        )
        self.db.add(self.bom)
        self.db.flush()
        self.db.add(models.BOMLine(
            bom_id=self.bom.id,
            material_id=self.material.id,
            qty_needed=3,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def warehouse_stock(self, item_id):
        return self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id,
            item_id=item_id,
        ).one().stock

    def make_order_and_process(self):
        order_response = assembly.create_order(
            assembly.AssemblyOrderCreate(
                date=date(2026, 8, 11),
                customer_id=self.customer.id,
                lines=[assembly.AssemblyOrderLineIn(bom_id=self.bom.id, qty_ordered=10)],
            ),
            self.db,
            self.user,
        )
        order = self.db.query(models.AssemblyCustomerOrder).get(order_response["id"])
        process_response = assembly.create_process(
            assembly.AssemblyProcessCreate(
                date=date(2026, 8, 11),
                order_id=order.id,
                warehouse_id=self.warehouse.id,
                lines=[assembly.AssemblyProcessLineIn(
                    order_line_id=order.lines[0].id,
                    qty_target=10,
                )],
            ),
            self.db,
            self.user,
        )
        process = self.db.query(models.AssemblyProcess).get(process_response["id"])
        return order, process

    def test_order_does_not_change_stock_and_process_consumes_material(self):
        order_response = assembly.create_order(
            assembly.AssemblyOrderCreate(
                date=date(2026, 8, 11),
                customer_id=self.customer.id,
                lines=[assembly.AssemblyOrderLineIn(bom_id=self.bom.id, qty_ordered=10)],
            ), self.db, self.user,
        )
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), 15)
        self.assertAlmostEqual(self.product.stock, 0)
        order = self.db.query(models.AssemblyCustomerOrder).get(order_response["id"])
        process_response = assembly.create_process(
            assembly.AssemblyProcessCreate(
                date=date(2026, 8, 11), order_id=order.id,
                warehouse_id=self.warehouse.id,
                lines=[assembly.AssemblyProcessLineIn(
                    order_line_id=order.lines[0].id, qty_target=10,
                )],
            ), self.db, self.user,
        )
        process = self.db.query(models.AssemblyProcess).get(process_response["id"])
        self.assertEqual(order.status, "processed")
        self.assertEqual(process.status, "in_progress")
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), 0)
        self.assertAlmostEqual(self.material.stock, 0)
        self.assertAlmostEqual(self.product.stock, 0)
        self.assertAlmostEqual(process.lines[0].material_cost_total, 30)
        self.assertAlmostEqual(process.lines[0].operational_cost_total, 20)

    def test_shortage_is_atomic(self):
        order_response = assembly.create_order(
            assembly.AssemblyOrderCreate(
                customer_id=self.customer.id,
                lines=[assembly.AssemblyOrderLineIn(bom_id=self.bom.id, qty_ordered=12)],
            ), self.db, self.user,
        )
        order = self.db.query(models.AssemblyCustomerOrder).get(order_response["id"])
        with self.assertRaises(HTTPException):
            assembly.create_process(
                assembly.AssemblyProcessCreate(
                    order_id=order.id,
                    warehouse_id=self.warehouse.id,
                    lines=[assembly.AssemblyProcessLineIn(
                        order_line_id=order.lines[0].id, qty_target=12,
                    )],
                ), self.db, self.user,
            )
        self.db.rollback()
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), 15)
        self.assertAlmostEqual(self.material.stock, 15)
        self.assertEqual(self.db.query(models.AssemblyProcess).count(), 0)

    def test_multi_product_document_completes_together(self):
        second_product = models.Item(code="PROD2", name="Produk Jadi Dua", stock=0)
        self.db.add(second_product)
        self.db.flush()
        second_bom = models.BillOfMaterial(
            product_id=second_product.id, qty_produced=1,
            operational_cost=0, is_active=True,
        )
        self.db.add(second_bom)
        self.db.flush()
        self.db.add(models.BOMLine(
            bom_id=second_bom.id, material_id=self.material.id, qty_needed=1,
        ))
        stock_row = self.db.query(models.WarehouseStock).filter_by(
            warehouse_id=self.warehouse.id, item_id=self.material.id,
        ).one()
        stock_row.stock += 10
        self.material.stock += 10
        add_batch(
            self.db, item_id=self.material.id, warehouse_id=self.warehouse.id,
            qty=10, unit_cost=2, received_date=date(2026, 1, 2),
        )
        self.db.commit()

        order_response = assembly.create_order(
            assembly.AssemblyOrderCreate(
                customer_id=self.customer.id,
                lines=[
                    assembly.AssemblyOrderLineIn(bom_id=self.bom.id, qty_ordered=4),
                    assembly.AssemblyOrderLineIn(bom_id=second_bom.id, qty_ordered=5),
                ],
            ), self.db, self.user,
        )
        order = self.db.query(models.AssemblyCustomerOrder).get(order_response["id"])
        process_response = assembly.create_process(
            assembly.AssemblyProcessCreate(
                order_id=order.id,
                warehouse_id=self.warehouse.id,
                lines=[
                    assembly.AssemblyProcessLineIn(order_line_id=order.lines[0].id, qty_target=4),
                    assembly.AssemblyProcessLineIn(order_line_id=order.lines[1].id, qty_target=5),
                ],
            ), self.db, self.user,
        )
        process = self.db.query(models.AssemblyProcess).get(process_response["id"])
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), 14)
        response = assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                close_process=True,
                lines=[
                    assembly.AssemblyResultLineIn(process_line_id=process.lines[0].id, qty_finished=4),
                    assembly.AssemblyResultLineIn(process_line_id=process.lines[1].id, qty_finished=5),
                ],
            ), self.db, self.user,
        )
        self.assertEqual(response["process_status"], "completed")
        self.assertAlmostEqual(self.warehouse_stock(self.product.id), 4)
        self.assertAlmostEqual(self.warehouse_stock(second_product.id), 5)

    def test_partial_then_short_close_uses_actual_quantity_and_conserves_cost(self):
        _, process = self.make_order_and_process()
        first = assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                date=date(2026, 8, 12),
                close_process=False,
                lines=[assembly.AssemblyResultLineIn(
                    process_line_id=process.lines[0].id,
                    qty_finished=6,
                )],
            ),
            self.db,
            self.user,
        )
        self.assertEqual(first["process_status"], "partially_completed")
        self.assertAlmostEqual(self.warehouse_stock(self.product.id), 6)

        with self.assertRaises(HTTPException) as ctx:
            assembly.create_result(
                assembly.AssemblyResultCreate(
                    process_id=process.id,
                    date=date(2026, 8, 13),
                    close_process=True,
                    confirm_variance=False,
                    lines=[assembly.AssemblyResultLineIn(
                        process_line_id=process.lines[0].id,
                        qty_finished=3,
                    )],
                ),
                self.db,
                self.user,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()

        final = assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                date=date(2026, 8, 13),
                close_process=True,
                confirm_variance=True,
                lines=[assembly.AssemblyResultLineIn(
                    process_line_id=process.lines[0].id,
                    qty_finished=3,
                )],
            ),
            self.db,
            self.user,
        )
        self.db.refresh(process)
        self.assertEqual(final["process_status"], "completed")
        self.assertAlmostEqual(process.lines[0].qty_completed, 9)
        self.assertAlmostEqual(process.lines[0].allocated_cost, 50)
        self.assertAlmostEqual(self.warehouse_stock(self.product.id), 9)
        batches = self.db.query(models.StockBatch).filter_by(
            warehouse_id=self.warehouse.id,
            item_id=self.product.id,
        ).all()
        self.assertAlmostEqual(sum(b.qty_remaining * b.unit_cost for b in batches), 50)

    def test_overproduction_requires_confirmation_and_keeps_total_cost(self):
        _, process = self.make_order_and_process()
        with self.assertRaises(HTTPException) as ctx:
            assembly.create_result(
                assembly.AssemblyResultCreate(
                    process_id=process.id,
                    close_process=True,
                    lines=[assembly.AssemblyResultLineIn(
                        process_line_id=process.lines[0].id,
                        qty_finished=12,
                    )],
                ), self.db, self.user,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()

        assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                close_process=True,
                confirm_variance=True,
                lines=[assembly.AssemblyResultLineIn(
                    process_line_id=process.lines[0].id,
                    qty_finished=12,
                )],
            ), self.db, self.user,
        )
        batches = self.db.query(models.StockBatch).filter_by(
            warehouse_id=self.warehouse.id,
            item_id=self.product.id,
        ).all()
        self.assertAlmostEqual(self.warehouse_stock(self.product.id), 12)
        self.assertAlmostEqual(sum(b.qty_remaining * b.unit_cost for b in batches), 50)

    def test_reverse_results_then_cancel_process_restores_fifo_material(self):
        order, process = self.make_order_and_process()
        first = assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                date=date(2026, 8, 12),
                lines=[assembly.AssemblyResultLineIn(
                    process_line_id=process.lines[0].id,
                    qty_finished=6,
                )],
            ), self.db, self.user,
        )
        final = assembly.create_result(
            assembly.AssemblyResultCreate(
                process_id=process.id,
                date=date(2026, 8, 13),
                close_process=True,
                confirm_variance=True,
                lines=[assembly.AssemblyResultLineIn(
                    process_line_id=process.lines[0].id,
                    qty_finished=3,
                )],
            ), self.db, self.user,
        )
        assembly.reverse_result(final["id"], assembly.CancelIn(reason="Salah hasil"), self.db, self.user)
        assembly.reverse_result(first["id"], assembly.CancelIn(reason="Batalkan semua"), self.db, self.user)
        assembly.cancel_process(process.id, assembly.CancelIn(reason="Ulang produksi"), self.db, self.user)
        self.db.refresh(order)
        self.assertEqual(order.status, "open")
        self.assertAlmostEqual(self.warehouse_stock(self.product.id), 0)
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), 15)
        self.assertAlmostEqual(total_remaining(self.db, self.material.id, self.warehouse.id), 15)
        self.assertAlmostEqual(self.material.stock, 15)

    def test_process_is_scoped_to_active_branch(self):
        _, process = self.make_order_and_process()
        other = models.Branch(code="B2", name="Cabang Dua")
        self.db.add(other)
        self.db.flush()
        self.user.active_branch_id = other.id
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            assembly.get_process(process.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_legacy_migration_preserves_stock_without_reposting(self):
        legacy = models.Assembly(
            number="ASM202608110001",
            date=date(2026, 8, 11),
            bom_id=self.bom.id,
            qty_planned=5,
            qty_produced=10,
            status="done",
            created_by=self.user.id,
        )
        self.db.add(legacy)
        self.db.commit()
        material_before = self.warehouse_stock(self.material.id)
        product_before = self.product.stock

        self.assertEqual(assembly.migrate_legacy_assemblies(self.db), 1)
        self.assertEqual(assembly.migrate_legacy_assemblies(self.db), 0)
        migrated = self.db.query(models.AssemblyProcess).filter_by(
            legacy_assembly_id=legacy.id,
        ).one()
        self.assertTrue(migrated.is_legacy)
        self.assertEqual(migrated.status, "completed")
        self.assertEqual(len(migrated.results), 1)
        self.assertAlmostEqual(self.warehouse_stock(self.material.id), material_before)
        self.assertAlmostEqual(self.product.stock, product_before)


if __name__ == "__main__":
    unittest.main()
