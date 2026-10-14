import math
import unittest

from pydantic import ValidationError

from app.schemas import (
    InventoryDocumentCancel,
    InventoryDocumentLineCreate,
    InventoryReconciliationApply,
    InventoryReconciliationLineApply,
)


class InventorySchemaValidationTests(unittest.TestCase):
    def test_inventory_quantities_must_be_finite(self):
        for field in ("qty", "physical_qty"):
            for invalid in (math.inf, -math.inf, math.nan):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValidationError):
                        InventoryDocumentLineCreate(item_id=1, **{field: invalid})

    def test_inventory_costs_must_be_finite(self):
        for invalid in (math.inf, math.nan):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    InventoryDocumentLineCreate(item_id=1, unit_cost=invalid)
                with self.assertRaises(ValidationError):
                    InventoryReconciliationLineApply(item_id=1, unit_cost=invalid)

    def test_reasons_are_trimmed_and_cannot_be_whitespace(self):
        self.assertEqual(InventoryDocumentCancel(reason="  salah input  ").reason, "salah input")
        with self.assertRaises(ValidationError):
            InventoryDocumentCancel(reason="   ")
        self.assertEqual(
            InventoryReconciliationApply(
                warehouse_id=1, preview_token="token", reason="  audit bulanan  "
            ).reason,
            "audit bulanan",
        )
        with self.assertRaises(ValidationError):
            InventoryReconciliationApply(
                warehouse_id=1, preview_token="token", reason="   "
            )


if __name__ == "__main__":
    unittest.main()
