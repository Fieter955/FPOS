import unittest
import sys
import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Hindari memakai database/runtime config aplikasi saat test dijalankan mandiri.
config_stub = types.ModuleType("app.config")
config_stub.settings = types.SimpleNamespace(
    DATABASE_URL="sqlite:///:memory:",
    SECRET_KEY="test-secret",
    ALGORITHM="HS256",
    ACCESS_TOKEN_EXPIRE_MINUTES=60,
)
sys.modules.setdefault("app.config", config_stub)

from app.database import Base
from app import models
from app.schemas import SupplierCreate
from app.routes.suppliers import create_supplier


class TestSupplierCreateSchema(unittest.TestCase):
    def test_empty_ppn_values_are_normalized_to_non_ppn(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                supplier = SupplierCreate.model_validate(
                    {"name": "Supplier Uji", "PpnSupplier": value}
                )
                self.assertEqual(supplier.PpnSupplier, 0)
                self.assertIsNone(supplier.ppn_type)

    def test_missing_ppn_defaults_to_non_ppn(self):
        supplier = SupplierCreate.model_validate({"name": "Supplier Uji"})

        self.assertEqual(supplier.PpnSupplier, 0)
        self.assertIsNone(supplier.ppn_type)

    def test_numeric_ppn_is_preserved(self):
        supplier = SupplierCreate.model_validate(
            {
                "name": "Supplier Kena Pajak",
                "PpnSupplier": 11,
                "ppn_type": "included",
            }
        )

        self.assertEqual(supplier.PpnSupplier, 11)
        self.assertEqual(supplier.ppn_type, "included")


class TestCreateSupplier(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_create_supplier_with_empty_ppn_persists_as_non_ppn(self):
        payload = SupplierCreate.model_validate(
            {
                "name": "Supplier Uji Create",
                "PpnSupplier": None,
                "item_ids": [],
            }
        )

        created = create_supplier(payload, db=self.db, _=object())
        persisted = self.db.query(models.Supplier).filter_by(id=created.id).one()

        self.assertTrue(persisted.code.startswith("SUP-"))
        self.assertEqual(persisted.name, "Supplier Uji Create")
        self.assertEqual(persisted.PpnSupplier, 0)
        self.assertIsNone(persisted.ppn_type)


if __name__ == "__main__":
    unittest.main()
