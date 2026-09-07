import sys
import types
import unittest
from datetime import datetime, timezone

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
from app.routes.customers import get_customer_history, get_customers
from app.routes.suppliers import get_suppliers


# Route functions above already hold the lightweight dependencies they need;
# remove the temporary import stubs before the next test module is imported.
for _module_name in (
    "app.config",
    "pytz",
    "fastapi",
    "app.auth",
    "app.schemas",
):
    sys.modules.pop(_module_name, None)
for _module_name in ("app.routes.customers", "app.routes.suppliers"):
    sys.modules.pop(_module_name, None)


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

    def test_customers_can_be_filtered_by_group_and_sorted_by_lowest_deposit(self):
        group = models.CustomerGroup(name="Grosir", discount_percent=5)
        self.db.add(group)
        self.db.flush()
        alpha = self.db.query(models.Customer).filter_by(code="C-ALPHA").one()
        low = self.db.query(models.Customer).filter_by(code="C-LOW").one()
        alpha.group_id = group.id
        low.group_id = group.id
        self.db.commit()

        result = get_customers(
            search=None, active_only=True, sort="deposit_asc", group_id=group.id,
            without_group=False, skip=0, limit=100, db=self.db, _=object(),
        )

        self.assertEqual([customer.code for customer in result], ["C-LOW", "C-ALPHA"])

    def test_customer_history_includes_all_branches_and_running_balance(self):
        customer = self.db.query(models.Customer).filter_by(code="C-LOW").one()
        branch_a = models.Branch(code="B-A", name="Cabang A")
        branch_b = models.Branch(code="B-B", name="Cabang B")
        self.db.add_all([branch_a, branch_b])
        self.db.flush()
        sale_a = models.Sale(
            number="SALE-A", date=datetime(2026, 8, 1).date(), customer_id=customer.id,
            total=150, paid=150, status="paid", branch_id=branch_a.id,
        )
        sale_b = models.Sale(
            number="SALE-B", date=datetime(2026, 8, 2).date(), customer_id=customer.id,
            total=200, paid=200, status="paid", branch_id=branch_b.id,
        )
        self.db.add_all([sale_a, sale_b])
        self.db.flush()
        self.db.add(models.CustomerDepositMovement(
            customer_id=customer.id, branch_id=branch_b.id, event_type="purchase",
            source_type="sale", source_id=sale_b.id, reference=sale_b.number,
            transaction_date=sale_b.date, occurred_at=datetime(2026, 8, 2, 10),
            amount=-50, balance_after=50, description="Deposit dipakai",
        ))
        self.db.commit()

        result = get_customer_history(
            cid=customer.id, start_date=None, end_date=None, event_type=None,
            branch_id=None, skip=0, limit=100, db=self.db, _=object(),
        )

        self.assertEqual({row["name"] for row in result["branches"]}, {"Cabang A", "Cabang B"})
        sale_b_row = next(row for row in result["items"] if row["number"] == "SALE-B")
        self.assertEqual(sale_b_row["deposit_change"], -50)
        self.assertEqual(sale_b_row["balance_after"], 50)


if __name__ == "__main__":
    unittest.main()
