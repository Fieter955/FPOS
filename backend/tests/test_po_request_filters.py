import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.routes import po


class PurchaseOrderRequestFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.pusat = models.Branch(
            id=1,
            code="PUSAT",
            name="Toko Pusat",
            status="Toko Utama",
        )
        self.cabang_a = models.Branch(id=2, code="CAB-A", name="Cabang A")
        self.cabang_b = models.Branch(id=3, code="CAB-B", name="Cabang B")
        self.admin_pusat = models.User(
            username="admin-pusat-po",
            full_name="Admin Pusat",
            hashed_password="x",
            role="admin",
            branch_id=1,
            active_branch_id=1,
            is_active=True,
        )
        self.admin_cabang = models.User(
            username="admin-cabang-po",
            full_name="Admin Cabang",
            hashed_password="x",
            role="admin",
            branch_id=2,
            active_branch_id=2,
            is_active=True,
        )
        self.db.add_all(
            [
                self.pusat,
                self.cabang_a,
                self.cabang_b,
                self.admin_pusat,
                self.admin_cabang,
            ]
        )
        self.db.flush()

        self.db.add_all(
            [
                self._request("REQ-A-PENDING", date(2026, 8, 2), 2, "pending"),
                self._request("REQ-A-DONE", date(2026, 8, 3), 2, "completed"),
                self._request("REQ-B-PENDING", date(2026, 8, 4), 3, "pending"),
                models.Purchase(
                    number="PURCHASE-A",
                    date=date(2026, 8, 5),
                    branch_id=2,
                    target_branch_id=1,
                    status="pending",
                    is_branch_request=False,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _request(number, request_date, branch_id, status):
        return models.Purchase(
            number=number,
            date=request_date,
            branch_id=branch_id,
            target_branch_id=1,
            status=status,
            document_type="branch_request",
            is_branch_request=True,
        )

    def get_requests(self, **overrides):
        params = {
            "status": "all",
            "start_date": None,
            "end_date": None,
            "branch_id": None,
            "db": self.db,
            "current_user": self.admin_pusat,
        }
        params.update(overrides)
        return po.get_incoming_requests(**params)

    def test_branch_filter_limits_requests_to_selected_origin(self):
        results = self.get_requests(branch_id=2)

        self.assertEqual(
            [request.number for request in results],
            ["REQ-A-DONE", "REQ-A-PENDING"],
        )

    def test_branch_filter_combines_with_status_and_dates(self):
        results = self.get_requests(
            branch_id=2,
            status="pending",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
        )

        self.assertEqual([request.number for request in results], ["REQ-A-PENDING"])
        self.assertEqual(self.get_requests(branch_id=999), [])

    def test_branch_user_cannot_view_incoming_requests(self):
        with self.assertRaises(HTTPException) as caught:
            self.get_requests(current_user=self.admin_cabang, branch_id=2)

        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
