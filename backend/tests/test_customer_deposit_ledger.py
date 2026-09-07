import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.services.customer_deposits import (
    apply_customer_deposit_change,
    backfill_customer_deposit_movements,
)


class CustomerDepositLedgerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_apply_change_updates_balance_and_ledger_atomically(self):
        customer = models.Customer(code="C-1", name="Pelanggan", deposit_balance=100)
        branch = models.Branch(code="B-1", name="Cabang Satu")
        self.db.add_all([customer, branch])
        self.db.flush()

        movement = apply_customer_deposit_change(
            self.db, customer, -30, event_type="purchase",
            transaction_date=date(2026, 9, 1), branch_id=branch.id,
            source_type="sale", source_id=7, reference="INV-7",
        )
        self.db.commit()

        self.assertEqual(customer.deposit_balance, 70)
        self.assertEqual(movement.amount, -30)
        self.assertEqual(movement.balance_after, 70)
        self.assertEqual(movement.branch.name, "Cabang Satu")

    def test_backfill_adds_opening_adjustment_and_is_idempotent(self):
        customer = models.Customer(code="C-2", name="Data Lama", deposit_balance=275)
        self.db.add(customer)
        self.db.commit()

        inserted = backfill_customer_deposit_movements(self.db)
        movement = self.db.query(models.CustomerDepositMovement).one()

        self.assertEqual(inserted, 1)
        self.assertEqual(movement.event_type, "opening_adjustment")
        self.assertEqual(movement.amount, 275)
        self.assertEqual(movement.balance_after, 275)
        self.assertEqual(backfill_customer_deposit_movements(self.db), 0)


if __name__ == "__main__":
    unittest.main()
