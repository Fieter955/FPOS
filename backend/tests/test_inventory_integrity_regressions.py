import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.routes.consignment import _block_legacy_consignment_mutation
from app.routes.returns import _purchase_return_unit_price, _sale_return_unit_price
from app.routes.trade_in import NewItemIn, TradeInCreate, create_trade_in, delete_trade_in
from app.routes.warehouse import TransferItemIn
from app.services.inventory_fifo import reduce_batches_for_reversal, transfer_batches


class InventoryIntegrityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _branch_warehouse(self, code, name):
        branch = models.Branch(code=code, name=name, status="Toko Utama")
        self.db.add(branch)
        self.db.flush()
        warehouse = models.Warehouse(
            code=f"W-{code}", name=f"Gudang {name}", branch_id=branch.id, is_default=True
        )
        self.db.add(warehouse)
        self.db.flush()
        return branch, warehouse

    def test_negative_transaction_numbers_are_rejected(self):
        with self.assertRaises(ValidationError):
            schemas.SaleItemCreate(item_id=1, qty=-1, sell_price=100)
        with self.assertRaises(ValidationError):
            schemas.PurchaseItemCreate(item_id=1, qty=1, buy_price=100, disc1=101)
        with self.assertRaises(ValidationError):
            TransferItemIn(item_id=1, qty=-2)

    def test_reversal_is_scoped_to_requested_warehouse(self):
        _, first = self._branch_warehouse("B1", "Satu")
        _, second = self._branch_warehouse("B2", "Dua")
        item = models.Item(code="I1", name="Item", stock=0)
        self.db.add(item)
        self.db.flush()
        self.db.add_all([
            models.StockBatch(item_id=item.id, warehouse_id=first.id, purchase_item_id=99,
                              unit_cost=10, qty_received=5, qty_remaining=5, received_date=date(2026, 1, 1)),
            models.StockBatch(item_id=item.id, warehouse_id=second.id, purchase_item_id=99,
                              unit_cost=10, qty_received=5, qty_remaining=5, received_date=date(2026, 1, 1)),
        ])
        self.db.flush()

        cost, leftover = reduce_batches_for_reversal(
            self.db, item_id=item.id, warehouse_id=second.id, qty=2, prefer_purchase_item_id=99
        )

        self.assertEqual(cost, 20)
        self.assertEqual(leftover, 0)
        self.assertEqual(self.db.query(models.StockBatch).filter_by(warehouse_id=first.id).one().qty_remaining, 5)
        self.assertEqual(self.db.query(models.StockBatch).filter_by(warehouse_id=second.id).one().qty_remaining, 3)

    def test_transfer_returns_actual_fifo_value(self):
        _, first = self._branch_warehouse("B1", "Satu")
        _, second = self._branch_warehouse("B2", "Dua")
        item = models.Item(code="I1", name="Item", stock=10, buy_price=30)
        self.db.add(item)
        self.db.flush()
        self.db.add_all([
            models.StockBatch(item_id=item.id, warehouse_id=first.id, unit_cost=10,
                              qty_received=5, qty_remaining=5, received_date=date(2026, 1, 1)),
            models.StockBatch(item_id=item.id, warehouse_id=first.id, unit_cost=20,
                              qty_received=5, qty_remaining=5, received_date=date(2026, 1, 2)),
        ])
        self.db.flush()
        self.assertEqual(
            transfer_batches(self.db, item_id=item.id, from_warehouse_id=first.id,
                             to_warehouse_id=second.id, qty=6),
            70,
        )

    def test_return_unit_prices_include_original_discounts(self):
        sale_line = SimpleNamespace(sell_price=111_000, discount=10, total=99_900)
        sale = SimpleNamespace(
            items=[sale_line], invoice_discount_gross=9_900, subtotal=90_000, discount=0
        )
        self.assertAlmostEqual(_sale_return_unit_price(sale, sale_line), 90_000)

        purchase_line = SimpleNamespace(qty=1, total=96_000, buy_price=100_000)
        purchase = SimpleNamespace(subtotal=96_000, discount=5_280)
        self.assertAlmostEqual(_purchase_return_unit_price(purchase, purchase_line), 90_720)

    def test_trade_in_uses_fifo_warehouse_tax_and_exact_tender(self):
        branch, warehouse = self._branch_warehouse("B1", "PKP")
        branch.is_pkp = True
        branch.tarif_ppn = 11
        user = models.User(username="u", hashed_password="x", branch_id=branch.id, active_branch_id=branch.id)
        customer = models.Customer(code="C1", name="Customer", deposit_balance=0)
        item = models.Item(code="I1", name="Item", stock=5, buy_price=30, sell_price=111, ppn_percent=11)
        self.db.add_all([user, customer, item])
        self.db.flush()
        self.db.add(models.WarehouseStock(warehouse_id=warehouse.id, item_id=item.id, stock=5))
        self.db.add(models.StockBatch(item_id=item.id, warehouse_id=warehouse.id, unit_cost=10,
                                      qty_received=5, qty_remaining=5, received_date=date(2026, 1, 1)))
        for code, name, account_type in [
            ("4-1100", "Penjualan", "revenue"), ("1-1400", "Persediaan", "asset"),
            ("5-1100", "HPP", "expense"), ("1-1100", "Kas", "asset"),
            ("1-1200", "Bank", "asset"), ("2-1300", "Deposit", "liability"),
            ("1-1450", "Karantina", "asset"), ("2-1200", "PPN", "liability"),
        ]:
            self.db.add(models.Account(code=code, name=name, type=account_type))
        self.db.commit()

        with self.assertRaises(HTTPException):
            create_trade_in(
                TradeInCreate(date=date(2026, 9, 7), customer_id=customer.id,
                              return_items=[], new_items=[NewItemIn(item_id=item.id, qty=1, sell_price=111)]),
                self.db, user,
            )
        self.db.rollback()

        result = create_trade_in(
            TradeInCreate(date=date(2026, 9, 7), customer_id=customer.id, cash_amount=111,
                          return_items=[], new_items=[NewItemIn(item_id=item.id, qty=1, sell_price=111)]),
            self.db, user,
        )
        self.assertEqual(self.db.query(models.WarehouseStock).one().stock, 4)
        self.assertEqual(self.db.query(models.StockBatch).one().qty_remaining, 4)
        revenue = self.db.query(models.Account).filter_by(code="4-1100").one()
        tax = self.db.query(models.Account).filter_by(code="2-1200").one()
        self.assertAlmostEqual(sum(line.amount for line in revenue.journal_credits), 100)
        self.assertAlmostEqual(sum(line.amount for line in tax.journal_credits), 11)

        delete_trade_in(result["id"], self.db, user)
        self.assertEqual(self.db.query(models.WarehouseStock).one().stock, 5)
        self.assertEqual(self.db.query(models.StockBatch).one().qty_remaining, 5)

    def test_legacy_consignment_mutation_is_blocked(self):
        with self.assertRaises(HTTPException) as error:
            _block_legacy_consignment_mutation()
        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
