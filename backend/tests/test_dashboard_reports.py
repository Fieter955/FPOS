import unittest
import sys
import types
from datetime import date
from datetime import timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Test endpoint ini sengaja tetap bisa berjalan di lingkungan CI ringan yang
# hanya memasang SQLAlchemy, sama seperti test_permissions.py.
config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(
    DATABASE_URL="sqlite:///:memory:",
    SECRET_KEY="test-secret",
    ALGORITHM="HS256",
)
sys.modules.setdefault("app.config", config_stub)


class _RouterStub:
    def get(self, *_args, **_kwargs):
        return lambda function: function


fastapi_stub = types.ModuleType("fastapi")
fastapi_stub.APIRouter = _RouterStub
fastapi_stub.Depends = lambda dependency=None: None
fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
fastapi_stub.Request = type("Request", (), {})
fastapi_stub.Response = type("Response", (), {})
sys.modules.setdefault("fastapi", fastapi_stub)

jose_stub = types.ModuleType("jose")
jose_stub.JWTError = type("JWTError", (Exception,), {})
jose_stub.jwt = types.SimpleNamespace(decode=lambda *args, **kwargs: {})
sys.modules.setdefault("jose", jose_stub)

pytz_stub = types.ModuleType("pytz")
pytz_stub.timezone = lambda _name: timezone.utc
sys.modules.setdefault("pytz", pytz_stub)

openpyxl_stub = types.ModuleType("openpyxl")
openpyxl_stub.Workbook = type("Workbook", (), {})
openpyxl_styles_stub = types.ModuleType("openpyxl.styles")
openpyxl_styles_stub.Font = type("Font", (), {})
openpyxl_styles_stub.PatternFill = type("PatternFill", (), {})
openpyxl_styles_stub.Alignment = type("Alignment", (), {})
sys.modules.setdefault("openpyxl", openpyxl_stub)
sys.modules.setdefault("openpyxl.styles", openpyxl_styles_stub)


def _get_query(db, model, current_user):
    query = db.query(model)
    if current_user.active_branch_id is not None and hasattr(model, "branch_id"):
        query = query.filter(
            (model.branch_id == current_user.active_branch_id)
            | (model.branch_id == None)
        )
    return query


auth_stub = types.ModuleType("app.auth")
auth_stub.get_current_user = lambda: None
auth_stub.get_query = _get_query
sys.modules.setdefault("app.auth", auth_stub)

accounting_stub = types.ModuleType("app.routes.accounting")
accounting_stub.get_income_statement = lambda **_kwargs: {"net_profit": 0}
sys.modules.setdefault("app.routes.accounting", accounting_stub)

from app.database import Base
from app import models
from app.routes import reports


class DashboardReportTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()

        self.branch = models.Branch(
            id=1,
            code="CBG-01",
            name="Cabang Uji",
            status="Toko Utama",
        )
        self.admin = models.User(
            username="admin-dashboard",
            full_name="Admin Dashboard",
            hashed_password="x",
            role="admin",
            branch_id=1,
            active_branch_id=1,
            is_active=True,
        )
        self.cashier = models.User(
            username="kasir-dashboard",
            full_name="Kasir Dashboard",
            hashed_password="x",
            role="kasir",
            branch_id=1,
            active_branch_id=1,
            is_active=True,
        )
        self.db.add_all([self.branch, self.admin, self.cashier])
        self.db.flush()
        self.db.add_all(
            [
                models.Sale(
                    number="INV-UJI-001",
                    date=date(2026, 7, 31),
                    total=125_000,
                    status="paid",
                    branch_id=1,
                    created_by=self.admin.id,
                ),
                models.Sale(
                    number="INV-UJI-002",
                    date=date(2026, 7, 31),
                    total=50_000,
                    status="cancelled",
                    branch_id=1,
                    created_by=self.admin.id,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_admin_dashboard_returns_kpis_and_financial_value(self):
        with (
            patch.object(reports, "get_local_date", return_value=date(2026, 7, 31)),
            patch.object(
                reports,
                "get_income_statement",
                return_value={"net_profit": 25_000},
            ) as income_statement,
        ):
            result = reports.get_dashboard_data(
                db=self.db,
                current_user=self.admin,
            )

        self.assertEqual(result["total_sales_today"], 125_000)
        self.assertEqual(result["total_transactions_today"], 1)
        self.assertEqual(result["total_purchases_today"], 0)
        self.assertEqual(result["net_profit_monthly"], 25_000)
        income_statement.assert_called_once()

    def test_sales_report_user_without_financial_access_still_gets_kpis(self):
        with (
            patch.object(reports, "get_local_date", return_value=date(2026, 7, 31)),
            patch.object(reports, "get_income_statement") as income_statement,
        ):
            result = reports.get_dashboard_data(
                db=self.db,
                current_user=self.cashier,
            )

        self.assertEqual(result["total_sales_today"], 125_000)
        self.assertEqual(result["total_transactions_today"], 1)
        self.assertEqual(result["net_profit_monthly"], 0)
        income_statement.assert_not_called()

    def test_day_without_transactions_returns_numeric_zeroes(self):
        with (
            patch.object(reports, "get_local_date", return_value=date(2026, 8, 1)),
            patch.object(
                reports,
                "get_income_statement",
                return_value={"net_profit": 0},
            ),
        ):
            result = reports.get_dashboard_data(
                db=self.db,
                current_user=self.admin,
            )

        self.assertEqual(result["total_sales_today"], 0)
        self.assertEqual(result["total_transactions_today"], 0)
        self.assertEqual(result["total_purchases_today"], 0)


if __name__ == "__main__":
    unittest.main()
