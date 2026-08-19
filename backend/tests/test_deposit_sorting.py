import sys
import types
import unittest
from datetime import timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(
    DATABASE_URL="sqlite:///:memory:",
    SECRET_KEY="test-secret",
    ALGORITHM="HS256",
    ACCESS_TOKEN_EXPIRE_MINUTES=60,
)
sys.modules.setdefault("app.config", config_stub)

pytz_stub = types.ModuleType("pytz")
pytz_stub.timezone = lambda _name: timezone.utc
sys.modules.setdefault("pytz", pytz_stub)


class _RouterStub:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    post = get
    put = get
    delete = get


fastapi_stub = types.ModuleType("fastapi")
fastapi_stub.APIRouter = _RouterStub
fastapi_stub.Depends = lambda dependency=None: None
fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
fastapi_stub.status = types.SimpleNamespace(HTTP_201_CREATED=201)
sys.modules.setdefault("fastapi", fastapi_stub)

auth_stub = types.ModuleType("app.auth")
auth_stub.get_current_user = lambda: None
auth_stub.require_admin = lambda: None
sys.modules.setdefault("app.auth", auth_stub)

schemas_stub = types.ModuleType("app.schemas")
for schema_name in (
    "CustomerCreate",
    "CustomerGroupCreate",
    "CustomerGroupOut",
    "CustomerOut",
    "CustomerTransferBalance",
    "CustomerUpdate",
    "ItemOut",
    "SalesPersonCreate",
    "SalesPersonOut",
    "SupplierCreate",
    "SupplierListOut",
    "SupplierOut",
    "SupplierPpnApply",
    "SupplierUpdate",
):
    setattr(schemas_stub, schema_name, type(schema_name, (), {}))
sys.modules.setdefault("app.schemas", schemas_stub)

from app import models
from app.database import Base
from app.routes.customers import get_customers
from app.routes.suppliers import get_suppliers


class DepositSortingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.db.add_all(
            [
                models.Customer(
                    code="C-LOW",
                    name="Pelanggan Saldo Kecil",
                    deposit_balance=100,
                    is_active=True,
                ),
                models.Customer(
                    code="C-BETA",
                    name="Beta Pelanggan",
                    deposit_balance=500,
                    is_active=True,
                ),
                models.Customer(
                    code="C-ALPHA",
                    name="Alpha Pelanggan",
                    deposit_balance=500,
                    is_active=True,
                ),
                models.Customer(
                    code="C-INACTIVE",
                    name="Pelanggan Nonaktif",
                    deposit_balance=999,
                    is_active=False,
                ),
                models.Supplier(
                    code="S-LOW",
                    name="Supplier Saldo Kecil",
                    deposit_balance=200,
                    is_active=True,
                ),
                models.Supplier(
                    code="S-HIGH",
                    name="Supplier Saldo Besar",
                    deposit_balance=800,
                    is_active=True,
                ),
                models.Supplier(
                    code="S-ZERO",
                    name="Supplier Saldo Nol",
                    deposit_balance=0,
                    is_active=True,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_customers_can_be_sorted_by_deposit_descending(self):
        result = get_customers(
            search=None,
            active_only=True,
            sort="deposit_desc",
            skip=0,
            limit=100,
            db=self.db,
            _=object(),
        )

        self.assertEqual(
            [customer.name for customer in result],
            ["Alpha Pelanggan", "Beta Pelanggan", "Pelanggan Saldo Kecil"],
        )

    def test_suppliers_can_be_sorted_by_deposit_descending(self):
        result = get_suppliers(
            search=None,
            active_only=True,
            sort="deposit_desc",
            skip=0,
            limit=100,
            db=self.db,
            _=object(),
        )

        self.assertEqual(
            [supplier.name for supplier in result],
            [
                "Supplier Saldo Besar",
                "Supplier Saldo Kecil",
                "Supplier Saldo Nol",
            ],
        )


if __name__ == "__main__":
    unittest.main()
