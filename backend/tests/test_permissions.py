import unittest
import sys
import types
from datetime import timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Test service murni ini tidak membutuhkan server FastAPI/Pydantic. Lingkungan
# CI ringan proyek dapat menjalankannya hanya dengan SQLAlchemy.
config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(
    DATABASE_URL="sqlite:///:memory:",
    SECRET_KEY="test-secret",
    ALGORITHM="HS256",
)
sys.modules.setdefault("app.config", config_stub)

fastapi_stub = types.ModuleType("fastapi")
fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
fastapi_stub.Request = type("Request", (), {})
sys.modules.setdefault("fastapi", fastapi_stub)

jose_stub = types.ModuleType("jose")
jose_stub.JWTError = type("JWTError", (Exception,), {})
jose_stub.jwt = types.SimpleNamespace(decode=lambda *args, **kwargs: {})
sys.modules.setdefault("jose", jose_stub)

pytz_stub = types.ModuleType("pytz")
pytz_stub.timezone = lambda _name: timezone.utc
sys.modules.setdefault("pytz", pytz_stub)

from app.database import Base
from app import models
from app.permission_catalog import AVAILABLE_GRANTS
from app.permissions import (
    effective_grants,
    has_permission,
    request_permission,
    seed_roles_and_permissions,
)


class PermissionServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.db.add_all(
            [
                models.User(
                    username="admin",
                    full_name="Administrator",
                    hashed_password="x",
                    role="admin",
                    is_active=True,
                ),
                models.User(
                    username="cashier",
                    full_name="Kasir",
                    hashed_password="x",
                    role="kasir",
                    is_active=True,
                ),
                models.User(
                    username="legacy",
                    full_name="Pegawai Lama",
                    hashed_password="x",
                    role="gudang",
                    is_active=True,
                ),
            ]
        )
        self.db.commit()
        seed_roles_and_permissions(self.db)

    def tearDown(self):
        self.db.close()

    def user(self, username):
        return self.db.query(models.User).filter_by(username=username).one()

    def test_admin_always_has_every_available_grant(self):
        self.assertEqual(effective_grants(self.db, self.user("admin")), AVAILABLE_GRANTS)

    def test_legacy_non_admin_keeps_operational_access_but_not_admin_grants(self):
        cashier = self.user("cashier")
        self.assertTrue(has_permission(self.db, cashier, "sales.cashier", "view"))
        self.assertTrue(has_permission(self.db, cashier, "purchase.transaction", "create"))
        self.assertFalse(
            has_permission(self.db, cashier, "settings.user_management", "access")
        )
        self.assertFalse(has_permission(self.db, cashier, "report.financial", "view"))
        self.assertTrue(has_permission(self.db, cashier, "master.warehouse", "view"))
        self.assertFalse(has_permission(self.db, cashier, "master.warehouse", "create"))

    def test_new_role_starts_empty_and_multi_role_is_union(self):
        limited = models.Role(
            name="limited",
            description="[permissions-seeded] Role kustom FPOS",
        )
        self.db.add(limited)
        self.db.flush()
        self.db.add(
            models.RolePermission(
                role_id=limited.id,
                permission_key="report.financial",
                action="view",
            )
        )
        user = models.User(
            username="multi",
            full_name="Multi Role",
            hashed_password="x",
            role="limited,kasir",
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.assertTrue(has_permission(self.db, user, "report.financial", "view"))
        self.assertTrue(has_permission(self.db, user, "sales.cashier", "view"))
        self.assertFalse(
            has_permission(self.db, user, "settings.user_management", "access")
        )

    def test_request_mapping_uses_semantic_actions(self):
        self.assertEqual(
            request_permission("/api/sales/10/cancel", "POST"),
            ("sales.cancel_detail", "view"),
        )
        self.assertEqual(
            request_permission("/api/purchases/5/pay", "POST"),
            ("purchase.payable", "update"),
        )
        self.assertEqual(
            request_permission("/api/employees/roles", "GET"),
            ("settings.user_management", "access"),
        )
        self.assertEqual(
            request_permission("/api/accounting/book-close/close", "POST"),
            ("accounting.annual_process", "view"),
        )
        self.assertEqual(
            request_permission("/api/branches/", "POST"),
            ("master.warehouse", "create"),
        )
        self.assertEqual(
            request_permission("/api/assembly/orders", "POST"),
            ("assembly.order", "create"),
        )
        self.assertEqual(
            request_permission("/api/assembly/processes", "POST"),
            ("assembly.transaction", "create"),
        )
        self.assertEqual(
            request_permission("/api/assembly/results/3/reverse", "POST"),
            ("assembly.finished_goods", "update"),
        )
        self.assertIsNone(request_permission("/api/auth/login", "POST"))
        self.assertEqual(
            request_permission("/api/print/settings", "GET"),
            ("settings.general", "access"),
        )
        self.assertEqual(
            request_permission("/api/print/", "POST"),
            ("master.barcode", "view"),
        )
        self.assertIsNone(request_permission("/api/print/agent/claim", "POST"))
        self.assertIsNone(request_permission("/api/inventory/documents", "POST"))
        self.assertIsNone(request_permission("/api/inventory/documents/1/cancel", "POST"))
        self.assertEqual(
            request_permission("/api/license/developer/kill-switch", "POST"),
            ("__admin__", "access"),
        )


if __name__ == "__main__":
    unittest.main()
