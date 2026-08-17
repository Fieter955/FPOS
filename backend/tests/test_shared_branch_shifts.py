import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes import shifts
from app.services.shift_service import require_single_open_branch_shift


class SharedBranchShiftTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        self.branch_one = models.Branch(
            id=1,
            code="CBG-01",
            name="Cabang Satu",
        )
        self.branch_two = models.Branch(
            id=2,
            code="CBG-02",
            name="Cabang Dua",
        )
        self.cashier_one = models.User(
            username="kasir-satu",
            full_name="Kasir Satu",
            hashed_password="x",
            role="kasir",
            branch_id=1,
            active_branch_id=1,
            is_active=True,
        )
        self.cashier_two = models.User(
            username="kasir-dua",
            full_name="Kasir Dua",
            hashed_password="x",
            role="kasir",
            branch_id=1,
            active_branch_id=1,
            is_active=True,
        )
        self.other_branch_cashier = models.User(
            username="kasir-cabang-dua",
            full_name="Kasir Cabang Dua",
            hashed_password="x",
            role="kasir",
            branch_id=2,
            active_branch_id=2,
            is_active=True,
        )
        self.db.add_all(
            [
                self.branch_one,
                self.branch_two,
                self.cashier_one,
                self.cashier_two,
                self.other_branch_cashier,
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_second_cashier_uses_and_can_close_shared_shift(self):
        opened = shifts.open_shift(
            {"opening_cash": 150_000},
            db=self.db,
            current_user=self.cashier_one,
        )

        current = shifts.get_current_shift(
            db=self.db,
            current_user=self.cashier_two,
        )
        self.assertEqual(current["id"], opened["id"])
        self.assertEqual(current["user_id"], self.cashier_one.id)
        self.assertFalse(current["has_conflict"])
        self.assertEqual(current["open_shift_count"], 1)
        self.assertEqual(
            require_single_open_branch_shift(self.db, self.cashier_two).id,
            opened["id"],
        )
        open_audit = (
            self.db.query(models.AuditLog)
            .filter(
                models.AuditLog.table_name == "shifts",
                models.AuditLog.record_id == opened["id"],
                models.AuditLog.action == "CREATE",
            )
            .one()
        )
        self.assertEqual(open_audit.user_id, self.cashier_one.id)

        result = shifts.close_shift(
            opened["id"],
            {"closing_cash": 150_000, "notes": "Tutup bersama"},
            db=self.db,
            current_user=self.cashier_two,
        )
        self.assertEqual(result["difference"], 0)

        audit = (
            self.db.query(models.AuditLog)
            .filter(
                models.AuditLog.table_name == "shifts",
                models.AuditLog.record_id == opened["id"],
                models.AuditLog.action == "UPDATE",
            )
            .one()
        )
        self.assertEqual(audit.user_id, self.cashier_two.id)
        self.assertIn("ditutup oleh Kasir Dua", audit.detail)

    def test_shift_is_isolated_from_other_branch(self):
        opened = shifts.open_shift(
            {"opening_cash": 10_000},
            db=self.db,
            current_user=self.cashier_one,
        )
        self.assertIsNone(
            shifts.get_current_shift(
                db=self.db,
                current_user=self.other_branch_cashier,
            )
        )

        with self.assertRaises(HTTPException) as caught:
            shifts.close_shift(
                opened["id"],
                {"closing_cash": 10_000},
                db=self.db,
                current_user=self.other_branch_cashier,
            )
        self.assertEqual(caught.exception.status_code, 404)

    def test_open_is_rejected_when_branch_already_has_active_shift(self):
        shifts.open_shift(
            {"opening_cash": 20_000},
            db=self.db,
            current_user=self.cashier_one,
        )
        with self.assertRaises(HTTPException) as caught:
            shifts.open_shift(
                {"opening_cash": 30_000},
                db=self.db,
                current_user=self.cashier_two,
            )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("cabang ini sudah memiliki shift", caught.exception.detail.lower())

    def test_legacy_duplicate_open_shifts_are_reported_and_block_sales(self):
        self.db.add_all(
            [
                models.Shift(
                    user_id=self.cashier_one.id,
                    branch_id=1,
                    opening_cash=10_000,
                    status="open",
                ),
                models.Shift(
                    user_id=self.cashier_two.id,
                    branch_id=1,
                    opening_cash=20_000,
                    status="open",
                ),
            ]
        )
        self.db.commit()

        current = shifts.get_current_shift(
            db=self.db,
            current_user=self.cashier_one,
        )
        self.assertTrue(current["has_conflict"])
        self.assertEqual(current["open_shift_count"], 2)

        with self.assertRaises(HTTPException) as caught:
            require_single_open_branch_shift(self.db, self.cashier_two)
        self.assertEqual(caught.exception.status_code, 409)

    def test_branch_history_is_shared_between_cashiers(self):
        opened = shifts.open_shift(
            {"opening_cash": 50_000},
            db=self.db,
            current_user=self.cashier_one,
        )
        history = shifts.get_shifts(
            db=self.db,
            current_user=self.cashier_two,
        )
        self.assertEqual([entry["id"] for entry in history], [opened["id"]])


if __name__ == "__main__":
    unittest.main()
