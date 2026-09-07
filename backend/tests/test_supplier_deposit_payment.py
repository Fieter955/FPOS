import unittest
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.database import Base
from app.routes.purchases import pay_purchase
from app.services.purchase_flow import cancel_purchase_flow


class SupplierDepositPaymentTests(unittest.TestCase):
    def test_desktop_safe_migration_contains_purchase_deposit_column(self):
        main_source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            'add_col("purchases", "deposit_paid", "REAL NOT NULL DEFAULT 0")',
            main_source,
        )

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        branch = models.Branch(code="B-DEP", name="Toko Deposit")
        supplier = models.Supplier(
            code="S-DEP", name="Supplier Deposit", deposit_balance=150
        )
        user = models.User(
            username="deposit-user",
            hashed_password="test",
            role="admin",
            branch=branch,
        )
        self.db.add_all([branch, supplier, user])
        self.db.flush()
        user.active_branch_id = branch.id
        self.db.add_all(
            [
                models.Account(code="1-1100", name="Kas", type="asset"),
                models.Account(code="1-1200", name="Bank", type="asset"),
                models.Account(code="1-1400", name="Persediaan", type="asset"),
                models.Account(code="1-1600", name="Saldo Supplier", type="asset"),
                models.Account(code="2-1100", name="Hutang Usaha", type="liability"),
            ]
        )
        purchase = models.Purchase(
            number="PUR-DEP-1",
            date=date.today(),
            supplier=supplier,
            total=100,
            paid=0,
            status="unpaid",
            branch=branch,
        )
        self.db.add(purchase)
        self.db.commit()
        self.user = user
        self.supplier = supplier
        self.purchase = purchase

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_deposit_pays_payable_and_is_restored_when_cancelled(self):
        result = pay_purchase(
            self.purchase.id,
            schemas.PurchasePayment(amount=60, deposit_amount=60),
            db=self.db,
            current_user=self.user,
        )

        self.assertEqual(result["deposit_amount"], 60)
        self.assertEqual(self.purchase.paid, 60)
        self.assertEqual(self.purchase.deposit_paid, 60)
        self.assertEqual(self.supplier.deposit_balance, 90)
        payment_journal = self.db.query(models.Journal).order_by(models.Journal.id.desc()).first()
        payment_lines = {
            (line.debit_account.code if line.debit_account else line.credit_account.code): (
                "debit" if line.debit_account else "credit",
                line.amount,
            )
            for line in payment_journal.lines
        }
        self.assertEqual(payment_lines["2-1100"], ("debit", 60))
        self.assertEqual(payment_lines["1-1600"], ("credit", 60))

        cancel_purchase_flow(db=self.db, purchase=self.purchase, current_user=self.user)
        self.db.commit()
        self.assertEqual(self.supplier.deposit_balance, 150)
        self.assertEqual(self.purchase.status, "cancelled")

    def test_rejects_deposit_above_supplier_balance(self):
        self.supplier.deposit_balance = 50
        self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            pay_purchase(
                self.purchase.id,
                schemas.PurchasePayment(amount=60, deposit_amount=60),
                db=self.db,
                current_user=self.user,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.purchase.paid, 0)
        self.assertEqual(self.supplier.deposit_balance, 50)


if __name__ == "__main__":
    unittest.main()
